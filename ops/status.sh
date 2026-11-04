#!/usr/bin/env bash
# Foto do ambiente: serviços, instâncias, fila, DLQ e alarmes em ALARM.  Uso: ops/status.sh [regiao]
source "$(dirname "$0")/comum.sh" "${1:-}" >/dev/null
date -u +%T
aws ecs describe-services --cluster $C --services chat-api commerce-api order-worker \
  --query 'services[].[serviceName,desiredCount,runningCount,pendingCount,deployments[0].rolloutState]' --output text
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names $ASG \
  --query 'AutoScalingGroups[0].[MinSize,DesiredCapacity,MaxSize,length(Instances)]' --output text | sed 's/^/ASG min\/desejado\/max\/instâncias: /'
aws ecs list-container-instances --cluster $C --query 'length(containerInstanceArns)' --output text | sed 's/^/instâncias no cluster: /'
for q in "$Q_URL" "$DLQ_URL"; do
  aws sqs get-queue-attributes --queue-url "$q" --attribute-names ApproximateNumberOfMessages --query Attributes.ApproximateNumberOfMessages --output text | sed "s|^|${q##*/}: |"
done
aws cloudwatch describe-alarms --alarm-name-prefix $P --state-value ALARM --query 'MetricAlarms[].AlarmName' --output text | sed 's/^/ALARM: /'
curl -s -m 5 "http://$ALB_DNS/health"; echo
