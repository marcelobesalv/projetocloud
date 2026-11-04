#!/usr/bin/env bash
# Perda de instância: encerra uma instância do cluster (como se tivesse falhado) e acompanha a recuperação.
# Rode a carga em paralelo para medir disponibilidade:  python3 ops/carga.py http://$ALB_DNS 600 6 > carga.log &
# Uso: ops/teste-instancia.sh [regiao]
set -euo pipefail
source "$(dirname "$0")/comum.sh" "${1:-}"
# a instância com mais tasks (o teste mais exigente)
CI=$(aws ecs describe-container-instances --cluster $C --container-instances $(aws ecs list-container-instances --cluster $C --query containerInstanceArns --output text) \
  --query 'reverse(sort_by(containerInstances,&runningTasksCount))[0].containerInstanceArn' --output text)
EC2=$(aws ecs describe-container-instances --cluster $C --container-instances $CI --query 'containerInstances[0].ec2InstanceId' --output text)
echo "tasks na instância $EC2:"
aws ecs list-tasks --cluster $C --container-instance $CI --query 'taskArns' --output text | tr '\t' '\n' | sed 's/.*\//  /'
echo "== $(date -u +%T) encerrando $EC2 (sem passar pelo ASG nem pelo ECS)"
aws ec2 terminate-instances --instance-ids $EC2 --query 'TerminatingInstances[0].CurrentState.Name' --output text
for i in $(seq 40); do
  sleep 20
  echo "--- $(date -u +%T)"
  "$(dirname "$0")/status.sh" "$AWS_DEFAULT_REGION" | sed -n '2,6p'
  OK=$(aws ecs describe-services --cluster $C --services chat-api commerce-api order-worker \
    --query 'length(services[?runningCount==desiredCount && length(deployments)==`1`])' --output text)
  N=$(aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names $ASG \
    --query 'length(AutoScalingGroups[0].Instances[?LifecycleState==`InService`])' --output text)
  D=$(aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names $ASG --query 'AutoScalingGroups[0].DesiredCapacity' --output text)
  if [ "$OK" = 3 ] && [ "$N" = "$D" ] && [ $i -gt 3 ]; then echo "== $(date -u +%T) recuperado: serviços estáveis e $N/$D instâncias"; break; fi
done
aws autoscaling describe-scaling-activities --auto-scaling-group-name $ASG --max-items 3 \
  --query 'Activities[].[StartTime,StatusCode,Description]' --output text
