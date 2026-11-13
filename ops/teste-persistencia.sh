#!/usr/bin/env bash
# Persistência: faz um pedido pelo chat, troca TODAS as tasks dos três serviços (force-new-deployment) e confere,
# na mesma conversa, que o histórico e o pedido continuam lá — nada essencial fica na task ou na instância.
# Uso: ops/teste-persistencia.sh [regiao]        (~10 min; usa o Bedrock no roteiro de demonstração)
set -euo pipefail
cd "$(dirname "$0")"
source ./comum.sh "${1:-}"
INICIO=$(($(date +%s) * 1000))
SAIDA=$(mktemp)

echo "== $(date -u +%T) 1. pedido pelo chat"
$PY demo.py "$URL" > "$SAIDA"
CONV=$(awk '/^conversa/{print $2}' "$SAIDA")
EV=$(aws logs filter-log-events --log-group-name "/ecs/$P/commerce-api" --start-time $INICIO \
  --filter-pattern '{ $.event = "order_created" }' --query 'events[].message' --output text \
  | tr '\t' '\n' | grep order_created | tail -n 1)  # a CLI pagina e aplica o --query por página
[ -z "$EV" ] && { echo "nenhum pedido criado:"; tail -n 20 "$SAIDA"; exit 1; }
PEDIDO=$($PY -c 'import json,sys; print(json.loads(sys.argv[1])["pedido_id"])' "$EV")
echo "   conversa $CONV · pedido $PEDIDO"

historico(){ $PY - "$URL" "$CONV" <<'PY'
import json, sys, urllib.request
h = json.load(urllib.request.urlopen(f"{sys.argv[1]}/conversations/{sys.argv[2]}", timeout=30))["mensagens"]
print(f"{len(h)} falas; última: " + h[-1]["texto"].replace("\n", " ")[:120])
PY
}
echo "   histórico antes: $(historico)"

echo "== $(date -u +%T) 2. trocando todas as tasks (chat-api, commerce-api, order-worker)"
ANTES=$(aws ecs list-tasks --cluster $C --desired-status RUNNING --query 'taskArns' --output text | tr '\t' '\n' | sort)
for s in commerce-api order-worker chat-api; do
  aws ecs update-service --cluster $C --service $s --force-new-deployment --query 'service.serviceName' --output text
done
aws ecs wait services-stable --cluster $C --services commerce-api order-worker chat-api
DEPOIS=$(aws ecs list-tasks --cluster $C --desired-status RUNNING --query 'taskArns' --output text | tr '\t' '\n' | sort)
echo "   tasks que sobreviveram à troca: $(comm -12 <(echo "$ANTES") <(echo "$DEPOIS") | grep -c . || true) (esperado: 0)"

echo "== $(date -u +%T) 3. mesma conversa depois da troca"
echo "   histórico depois: $(historico)"
$PY - "$URL" "$CONV" <<'PY'
import json, sys, urllib.request
req = urllib.request.Request(f"{sys.argv[1]}/chat", json.dumps({"conversation_id": sys.argv[2],
      "message": "Qual o status do pedido?"}).encode(), {"Content-Type": "application/json"})
print("   chat:", json.load(urllib.request.urlopen(req, timeout=70))["resposta"].replace("\n", " ")[:300])
PY
rm -f "$SAIDA"
