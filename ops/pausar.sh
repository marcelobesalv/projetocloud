#!/usr/bin/env bash
# Desliga o que custa por hora (tasks, instâncias, RDS). NAT e ALB continuam.  Uso: ops/pausar.sh <regiao>...
for R in "$@"; do
  export AWS_DEFAULT_REGION=$R; echo "== $R"
  for s in chat-api commerce-api order-worker; do
    aws application-autoscaling register-scalable-target --service-namespace ecs --scalable-dimension ecs:service:DesiredCount \
      --resource-id service/setup-pronto-cluster/$s --min-capacity 0 --max-capacity 0 >/dev/null
    aws ecs update-service --cluster setup-pronto-cluster --service $s --desired-count 0 --query 'service.[serviceName,desiredCount]' --output text
  done
  aws autoscaling update-auto-scaling-group --auto-scaling-group-name setup-pronto-ecs-asg --min-size 0 && echo "ASG min 0"
  aws rds stop-db-instance --db-instance-identifier setup-pronto-db --query DBInstance.DBInstanceStatus --output text
done
