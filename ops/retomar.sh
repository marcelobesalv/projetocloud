#!/usr/bin/env bash
# Religa um ambiente pausado com ops/pausar.sh (em São Paulo, um "terraform apply" também restaura tudo).
# Uso: ops/retomar.sh <regiao>
set -uo pipefail
export AWS_REGION=$1 AWS_DEFAULT_REGION=$1; C=setup-pronto-cluster
aws rds start-db-instance --db-instance-identifier setup-pronto-db --query DBInstance.DBInstanceStatus --output text
aws autoscaling update-auto-scaling-group --auto-scaling-group-name setup-pronto-ecs-asg --min-size 2 --max-size 4 --desired-capacity 2 && echo ASG_2
aws rds wait db-instance-available --db-instance-identifier setup-pronto-db && echo RDS_OK
reg(){ aws application-autoscaling register-scalable-target --service-namespace ecs --scalable-dimension ecs:service:DesiredCount \
  --resource-id service/$C/$1 --min-capacity $2 --max-capacity $3 >/dev/null; }
reg chat-api 2 6; reg commerce-api 2 6; reg order-worker 1 4
# commerce primeiro: o chat só enxerga o alias do Service Connect se ele já existir quando a task sobe
aws ecs update-service --cluster $C --service commerce-api --desired-count 2 --query service.serviceName --output text
aws ecs wait services-stable --cluster $C --services commerce-api
for s in order-worker chat-api; do aws ecs update-service --cluster $C --service $s --desired-count 2 --force-new-deployment --query service.serviceName --output text; done
aws ecs wait services-stable --cluster $C --services commerce-api order-worker chat-api || echo AINDA_NAO_ESTAVEL
"$(dirname "$0")/status.sh" "$1"
