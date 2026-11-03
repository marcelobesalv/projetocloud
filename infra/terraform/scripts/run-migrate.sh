#!/usr/bin/env bash
# Roda a task de migração uma vez e falha se o container não sair com código 0.
# Uso: run-migrate.sh <regiao> <cluster> <task-definition-arn> <capacity-provider>
set -euo pipefail
REGION=$1 CLUSTER=$2 TD=$3 CP=$4
export AWS_DEFAULT_REGION=$REGION

for i in $(seq 30); do   # espera alguma instância registrar no cluster (primeiro apply)
  n=$(aws ecs describe-clusters --clusters "$CLUSTER" --query 'clusters[0].registeredContainerInstancesCount' --output text)
  [ "$n" != "0" ] && break
  echo "aguardando instâncias no cluster ($i)..."; sleep 10
done

OUT=$(aws ecs run-task --cluster "$CLUSTER" --task-definition "$TD" \
  --capacity-provider-strategy "capacityProvider=$CP,weight=1" --started-by terraform-migrate --output json)
TASK=$(echo "$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["tasks"][0]["taskArn"] if d["tasks"] else "")')
if [ -z "$TASK" ]; then echo "run-task falhou: $OUT"; exit 1; fi
echo "migração: $TASK"
aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$TASK" || aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$TASK"
read -r CODE REASON < <(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK" \
  --query 'tasks[0].[containers[0].exitCode,stoppedReason]' --output text)
echo "exitCode=$CODE ($REASON)"
aws logs tail "/ecs/$(echo "$CLUSTER" | sed 's/-cluster$//')/migrate" --since 15m --format short | tail -15 || true
[ "$CODE" = "0" ]
