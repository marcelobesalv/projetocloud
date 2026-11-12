#!/usr/bin/env bash
# Desliga o que custa por hora (tasks, instâncias, RDS). NAT e ALB continuam.  Uso: ops/pausar.sh <regiao>...
# O RDS parado religa sozinho depois de 7 dias (limite da AWS): pausas longas precisam ser repetidas.
for R in "$@"; do
  export AWS_REGION=$R AWS_DEFAULT_REGION=$R; echo "== $R"
  for s in chat-api commerce-api order-worker; do
    aws application-autoscaling register-scalable-target --service-namespace ecs --scalable-dimension ecs:service:DesiredCount \
      --resource-id service/setup-pronto-cluster/$s --min-capacity 0 --max-capacity 0 >/dev/null
    aws ecs update-service --cluster setup-pronto-cluster --service $s --desired-count 0 --query 'service.[serviceName,desiredCount]' --output text
  done
  aws ecs wait services-stable --cluster setup-pronto-cluster --services chat-api commerce-api order-worker || true
  # Com zero tasks e zero instâncias o ECS calcula CapacityProviderReservation = 100%, acima do alvo de 90%, e o
  # capacity provider sobe uma instância; por isso o máximo do ASG vai a 0 (o retomar.sh e o Terraform restauram).
  # As instâncias têm proteção de scale-in (managed termination protection): sem tirá-la, o ASG não as encerra.
  aws autoscaling update-auto-scaling-group --auto-scaling-group-name setup-pronto-ecs-asg --min-size 0 --max-size 0 \
    --desired-capacity 0
  IDS=$(aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names setup-pronto-ecs-asg \
    --query 'AutoScalingGroups[0].Instances[].InstanceId' --output text)
  [ -n "$IDS" ] && aws autoscaling set-instance-protection --auto-scaling-group-name setup-pronto-ecs-asg \
    --instance-ids $IDS --no-protected-from-scale-in
  echo "ASG 0/0/0 (encerrando: ${IDS:-nenhuma})"
  aws rds stop-db-instance --db-instance-identifier setup-pronto-db --query DBInstance.DBInstanceStatus --output text
done
