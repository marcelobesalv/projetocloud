#!/usr/bin/env bash
# Drenagem: marca uma instância como DRAINING; o ECS move as tasks para as outras (respeitando o
# minimumHealthyPercent) antes de pará-las. Depois devolve a instância (ACTIVE) ou deixa o capacity provider
# removê-la (com ela vazia, a managed termination protection libera o scale-in).
# Uso: ops/drenar-instancia.sh [regiao] [--remover]
set -euo pipefail
source "$(dirname "$0")/comum.sh" "${1:-}"
# a instância com mais tasks (o teste mais exigente)
CI=$(aws ecs describe-container-instances --cluster $C --container-instances $(aws ecs list-container-instances --cluster $C --query containerInstanceArns --output text) \
  --query 'reverse(sort_by(containerInstances,&runningTasksCount))[0].containerInstanceArn' --output text)
EC2=$(aws ecs describe-container-instances --cluster $C --container-instances $CI --query 'containerInstances[0].ec2InstanceId' --output text)
echo "== $(date -u +%T) drenando $EC2"
aws ecs update-container-instances-state --cluster $C --container-instances $CI --status DRAINING --query 'containerInstances[0].status' --output text
for i in $(seq 30); do
  sleep 15
  N=$(aws ecs describe-container-instances --cluster $C --container-instances $CI --query 'containerInstances[0].runningTasksCount' --output text)
  echo "$(date -u +%T) tasks ainda na instância: $N"
  [ "$N" = 0 ] && break
done
"$(dirname "$0")/status.sh" "$AWS_DEFAULT_REGION"
if [ "${2:-}" = "--remover" ]; then
  echo "== instância vazia; encerrando pelo ASG e reduzindo o desejado"
  aws autoscaling set-instance-protection --auto-scaling-group-name $ASG --instance-ids $EC2 --no-protected-from-scale-in
  aws autoscaling terminate-instance-in-auto-scaling-group --instance-id $EC2 --should-decrement-desired-capacity \
    --query 'Activity.Description' --output text
else
  echo "== devolvendo a instância ao cluster"
  aws ecs update-container-instances-state --cluster $C --container-instances $CI --status ACTIVE --query 'containerInstances[0].status' --output text
fi
