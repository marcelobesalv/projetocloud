#!/usr/bin/env bash
# Substituição de task: para à força uma task (como se tivesse travado) e acompanha o ECS repor, com uma carga leve
# pelo ALB medindo se o cliente percebe. Sem chamadas ao LLM (só GET /).
# Uso: ops/teste-task.sh [regiao] [servico]        (padrão: chat-api; ~4 min)
set -euo pipefail
cd "$(dirname "$0")"
source ./comum.sh "${1:-}"
SVC=${2:-chat-api}
LOG=$(mktemp)

$PY carga.py "$URL" --degraus 2:240 --chat-cada 0 --janela 15 > "$LOG" 2>&1 &
CARGA=$!
sleep 20

TASK=$(aws ecs list-tasks --cluster $C --service-name $SVC --desired-status RUNNING --query 'taskArns[0]' --output text)
echo "== $(date -u +%T) parando a task ${TASK##*/} do $SVC"
aws ecs stop-task --cluster $C --task "$TASK" --reason "teste de substituicao de task" --query 'task.desiredStatus' --output text

TG=$(aws elbv2 describe-target-groups --names $P-chat-tg --query 'TargetGroups[0].TargetGroupArn' --output text)
for i in $(seq 24); do
  sleep 10
  read -r D R PEND < <(aws ecs describe-services --cluster $C --services $SVC \
    --query 'services[0].[desiredCount,runningCount,pendingCount]' --output text)
  H=$(aws elbv2 describe-target-health --target-group-arn "$TG" \
    --query 'length(TargetHealthDescriptions[?TargetHealth.State==`healthy`])' --output text)
  echo "   $(date -u +%T) $SVC desejado=$D rodando=$R pendente=$PEND · alvos saudáveis do chat no ALB=$H"
  [ "$R" = "$D" ] && [ "$PEND" = 0 ] && [ $i -ge 3 ] && break
done

echo "== eventos do serviço"
aws ecs describe-services --cluster $C --services $SVC --query 'services[0].events[:4].[createdAt,message]' --output text
echo "== carga durante o teste (cliente)"
wait $CARGA || true
sed -n '/resumo por degrau/,$p' "$LOG"
rm -f "$LOG"
