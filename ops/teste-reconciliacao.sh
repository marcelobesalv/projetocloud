#!/usr/bin/env bash
# Teste da reconciliação na AWS: um pedido cuja mensagem se perde volta sozinho para a fila e é processado.
#   1. tira o order-worker do ar (auto scaling 0/0) para a mensagem ficar parada na fila
#   2. faz um pedido pelo chat (roteiro do ops/demo.py, usa o Bedrock) → pedido PENDENTE
#   3. purga a fila "orders" (a mensagem do pedido se perde) e religa o worker com os limites de antes
#   4. espera o commerce-api reenviar (log order_requeued; ~RECONCILIACAO_IDADE_SECONDS = 10 min + até 1 ciclo)
#      e o worker processar (order_processed), e confere o status pelo chat
# Uso: ops/teste-reconciliacao.sh [regiao]        (~15 min; o alarme order-worker-tasks-abaixo-do-minimo dispara, é esperado)
set -euo pipefail
cd "$(dirname "$0")"
source ./comum.sh "${1:-}"
LG=/ecs/$P
INICIO=$(($(date +%s) * 1000))
alvo(){ aws application-autoscaling register-scalable-target --service-namespace ecs --scalable-dimension ecs:service:DesiredCount \
  --resource-id service/$C/order-worker --min-capacity $1 --max-capacity $2 >/dev/null; }
logs(){ aws logs filter-log-events --log-group-name "$LG/$1" --start-time $INICIO --filter-pattern "$2" \
  --query 'events[].message' --output text; }

read -r MIN MAX < <(aws application-autoscaling describe-scalable-targets --service-namespace ecs \
  --resource-ids service/$C/order-worker --query 'ScalableTargets[0].[MinCapacity,MaxCapacity]' --output text)
restaurar(){ alvo "$MIN" "$MAX"; aws ecs update-service --cluster $C --service order-worker --desired-count "$MIN" \
  --query 'service.[serviceName,desiredCount]' --output text; }
trap restaurar EXIT  # religa o worker mesmo se o script for interrompido

echo "== $(date -u +%T) 1. order-worker fora do ar (limites atuais: $MIN..$MAX)"
alvo 0 0
aws ecs update-service --cluster $C --service order-worker --desired-count 0 --query 'service.[serviceName,desiredCount]' --output text
aws ecs wait services-stable --cluster $C --services order-worker

echo "== $(date -u +%T) 2. pedido pelo chat"
$PY demo.py "$URL" | tee /tmp/demo-reconc.log | tail -n 8
CONV=$(awk '/^conversa/{print $2}' /tmp/demo-reconc.log)
sleep 5
EV=$(aws logs filter-log-events --log-group-name "$LG/commerce-api" --start-time $INICIO \
  --filter-pattern '{ $.event = "order_created" }' --query 'events[].message' --output text \
  | tr '\t' '\n' | grep order_created | tail -n 1)  # a CLI pagina e aplica o --query por página
[ -z "$EV" ] && { echo "nenhum pedido criado (veja a conversa acima)"; exit 1; }
PEDIDO=$($PY -c 'import json,sys; print(json.loads(sys.argv[1])["pedido_id"])' "$EV")
CID=$($PY -c 'import json,sys; print(json.loads(sys.argv[1]).get("correlation_id") or "")' "$EV")
echo "pedido $PEDIDO (correlation_id $CID), conversa $CONV"

echo "== $(date -u +%T) 3. fila antes da purga: $(aws sqs get-queue-attributes --queue-url "$Q_URL" \
  --attribute-names ApproximateNumberOfMessages --query Attributes.ApproximateNumberOfMessages --output text) mensagem(ns); purgando"
aws sqs purge-queue --queue-url "$Q_URL"
sleep 60  # a purga leva até 60 s
restaurar
trap - EXIT

echo "== $(date -u +%T) 4. esperando a reconciliação (até 20 min)"
for i in $(seq 80); do
  sleep 15
  if [ -n "$(logs commerce-api "{ \$.event = \"order_requeued\" && \$.pedido_id = \"$PEDIDO\" }")" ]; then
    echo "   $(date -u +%T) reenviado pelo commerce-api (order_requeued)"
    break
  fi
  [ $((i % 4)) = 0 ] && echo "   $(date -u +%T) ainda PENDENTE, aguardando o reenvio"
done
for i in $(seq 20); do
  sleep 10
  PROC=$(logs order-worker "{ \$.event = \"order_processed\" && \$.pedido_id = \"$PEDIDO\" }")
  if [ -n "$PROC" ]; then echo "   $(date -u +%T) processado pelo worker: $(echo "$PROC" | cut -c1-240)"; break; fi
done

echo "== status pelo chat"
$PY - "$URL" "$CONV" <<'PY'
import json, sys, urllib.request
req = urllib.request.Request(f"{sys.argv[1]}/chat", json.dumps({"conversation_id": sys.argv[2],
      "message": "Qual o status do pedido?"}).encode(), {"Content-Type": "application/json"})
print(json.load(urllib.request.urlopen(req, timeout=70))["resposta"][:400])
PY
echo "Logs do pedido em todos os serviços: CloudWatch Logs Insights com  filter correlation_id = \"$CID\""
