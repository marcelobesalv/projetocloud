#!/usr/bin/env bash
# Etapa manual (Ohio): o que o Terraform já cria em São Paulo — Container Insights, métricas do ASG e os alarmes
# de saúde das tasks, reserva do cluster e capacidade do ASG. Idempotente.  Uso: ops/ohio-alarmes-extra.sh
set -euo pipefail
export AWS_REGION=us-east-2 AWS_DEFAULT_REGION=us-east-2 # o CloudShell define AWS_REGION, que tem precedência
P=setup-pronto C=setup-pronto-cluster ASG=setup-pronto-ecs-asg
TOPIC=$(aws sns create-topic --name $P-alertas --query TopicArn --output text)
aws ecs update-cluster-settings --cluster $C --settings name=containerInsights,value=enabled --query 'cluster.settings' --output text
aws autoscaling enable-metrics-collection --auto-scaling-group-name $ASG --granularity 1Minute
al(){ # nome descrição namespace métrica estatística operador limite períodos período dimensões...
  local n=$1 d=$2 ns=$3 m=$4 st=$5 op=$6 lim=$7 ev=$8 per=$9; shift 9
  aws cloudwatch put-metric-alarm --alarm-name "$P-$n" --alarm-description "$d" --namespace "$ns" --metric-name "$m" \
    --statistic "$st" --comparison-operator "$op" --threshold "$lim" --evaluation-periods "$ev" --period "$per" \
    --treat-missing-data notBreaching --alarm-actions "$TOPIC" --ok-actions "$TOPIC" --dimensions "$@" && echo "alarme $n"
}
al cluster-cpu-reservada "Mais de 90% da CPU do cluster reservada por 15 min" AWS/ECS CPUReservation Average GreaterThanThreshold 90 3 300 Name=ClusterName,Value=$C
al cluster-memoria-reservada "Mais de 90% da memoria do cluster reservada por 15 min" AWS/ECS MemoryReservation Average GreaterThanThreshold 90 3 300 Name=ClusterName,Value=$C
al capacidade-insuficiente "Tasks esperando instancia por 15 min" AWS/ECS/ManagedScaling CapacityProviderReservation Average GreaterThanThreshold 100 3 300 Name=CapacityProviderName,Value=$P-cp Name=ClusterName,Value=$C
al asg-abaixo-do-minimo "Instancias em servico abaixo do minimo por 5 min" AWS/AutoScaling GroupInServiceInstances Minimum LessThanThreshold 2 5 60 Name=AutoScalingGroupName,Value=$ASG
al asg-no-maximo "ASG no tamanho maximo por 15 min" AWS/AutoScaling GroupInServiceInstances Minimum GreaterThanOrEqualToThreshold 4 3 300 Name=AutoScalingGroupName,Value=$ASG
for s in chat-api:2 commerce-api:2 order-worker:1; do
  al "${s%%:*}-tasks-abaixo-do-minimo" "${s%%:*} com menos de ${s##*:} task(s) por 3 min" ECS/ContainerInsights RunningTaskCount Minimum LessThanThreshold "${s##*:}" 3 60 \
    Name=ClusterName,Value=$C Name=ServiceName,Value=${s%%:*}
done
