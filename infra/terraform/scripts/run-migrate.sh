#!/usr/bin/env bash
# Roda a task de migração uma vez e falha se o container não sair com código 0.
# Uso: run-migrate.sh <regiao> <cluster> <task-definition-arn> <capacity-provider>
set -euo pipefail
REGION=$1 CLUSTER=$2 TD=$3 CP=$4
export AWS_REGION=$REGION AWS_DEFAULT_REGION=$REGION

for i in $(seq 30); do   # espera alguma instância registrar no cluster (primeiro apply)
  n=$(aws ecs describe-clusters --clusters "$CLUSTER" --query 'clusters[0].registeredContainerInstancesCount' --output text)
  [ "$n" != "0" ] && break
  echo "aguardando instâncias no cluster ($i)..."; sleep 10
done

# Num apply do zero, a política da execution role (ler os segredos) pode ter acabado de ser criada e ainda não ter
# propagado no IAM: a task para antes de o container rodar (exitCode None + "not authorized"/AccessDenied). Esse caso é
# repetido com espera; falha do próprio container (exitCode != 0) não é.
for tentativa in 1 2 3 4 5 6; do
  OUT=$(aws ecs run-task --cluster "$CLUSTER" --task-definition "$TD" \
    --capacity-provider-strategy "capacityProvider=$CP,weight=1" --started-by terraform-migrate --output json)
  TASK=$(echo "$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["tasks"][0]["taskArn"] if d["tasks"] else "")')
  if [ -z "$TASK" ]; then echo "run-task falhou: $OUT"; exit 1; fi
  echo "migração (tentativa $tentativa): $TASK"
  aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$TASK" || aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$TASK"
  read -r CODE REASON < <(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK" \
    --query 'tasks[0].[containers[0].exitCode,stoppedReason]' --output text)
  echo "exitCode=$CODE (${REASON:0:300})"
  if [ "$CODE" = None ] && [[ "$REASON" =~ (not\ authorized|AccessDenied) ]] && [ $tentativa -lt 6 ]; then
    echo "permissão do IAM ainda propagando; nova tentativa em 20 s"
    sleep 20
    continue
  fi
  break
done
aws logs tail "/ecs/$(echo "$CLUSTER" | sed 's/-cluster$//')/migrate" --since 15m --format short | tail -15 || true
[ "$CODE" = "0" ]
