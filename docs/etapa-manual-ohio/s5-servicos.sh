#!/bin/bash
# Bloco 5: namespace do Service Connect, ALB e os 3 servicos ECS
set -euo pipefail
source ~/sp.env
P=setup-pronto; R=us-east-2; C=$P-cluster
save(){ sed -i "/^$1=/d" ~/sp.env; echo "$1=$2" >> ~/sp.env; echo "$1=$2"; }

echo "== namespace $P.local"
NS_ID=$(aws servicediscovery list-namespaces --query "Namespaces[?Name=='$P.local'].Id" --output text)
if [ -z "$NS_ID" ]; then
  OP=$(aws servicediscovery create-http-namespace --name $P.local --tags Key=Project,Value=$P --query OperationId --output text)
  for i in $(seq 40); do
    ST=$(aws servicediscovery get-operation --operation-id $OP --query Operation.Status --output text)
    [ "$ST" = SUCCESS ] && break; [ "$ST" = FAIL ] && { echo "namespace falhou"; exit 1; }; sleep 3
  done
  NS_ID=$(aws servicediscovery get-operation --operation-id $OP --query Operation.Targets.NAMESPACE --output text)
fi
NS_ARN=$(aws servicediscovery get-namespace --id $NS_ID --query Namespace.Arn --output text); save NS_ARN $NS_ARN
aws ecs update-cluster --cluster $C --service-connect-defaults namespace=$NS_ARN --query cluster.clusterName --output text
aws logs create-log-group --log-group-name /ecs/$P/service-connect 2>/dev/null || true
aws logs put-retention-policy --log-group-name /ecs/$P/service-connect --retention-in-days 7

echo "== ALB"
ALB_ARN=$(aws elbv2 create-load-balancer --name $P-alb --type application --scheme internet-facing --subnets $PUB1 $PUB2 --security-groups $ALB_SG --tags Key=Project,Value=$P --query 'LoadBalancers[0].LoadBalancerArn' --output text); save ALB_ARN $ALB_ARN
aws elbv2 modify-load-balancer-attributes --load-balancer-arn $ALB_ARN --attributes Key=idle_timeout.timeout_seconds,Value=60 >/dev/null
TG_ARN=$(aws elbv2 create-target-group --name $P-chat-tg --protocol HTTP --port 8080 --vpc-id $VPC --target-type instance --health-check-path /health --health-check-interval-seconds 15 --health-check-timeout-seconds 5 --healthy-threshold-count 2 --unhealthy-threshold-count 3 --matcher HttpCode=200 --tags Key=Project,Value=$P --query 'TargetGroups[0].TargetGroupArn' --output text); save TG_ARN $TG_ARN
aws elbv2 modify-target-group-attributes --target-group-arn $TG_ARN --attributes Key=deregistration_delay.timeout_seconds,Value=30 >/dev/null
LST=$(aws elbv2 describe-listeners --load-balancer-arn $ALB_ARN --query 'Listeners[?Port==`80`].ListenerArn' --output text)
[ -n "$LST" ] || LST=$(aws elbv2 create-listener --load-balancer-arn $ALB_ARN --protocol HTTP --port 80 --default-actions Type=forward,TargetGroupArn=$TG_ARN --query 'Listeners[0].ListenerArn' --output text)
save LISTENER_ARN $LST
save ALB_DNS $(aws elbv2 describe-load-balancers --load-balancer-arns $ALB_ARN --query 'LoadBalancers[0].DNSName' --output text)

scl(){ printf '{"logDriver":"awslogs","options":{"awslogs-group":"/ecs/%s/service-connect","awslogs-region":"%s","awslogs-stream-prefix":"%s"}}' $P $R $1; }
criar(){ # nome desired [args extras...]
  local n=$1 d=$2; shift 2
  if [ "$(aws ecs describe-services --cluster $C --services $n --query 'services[0].status' --output text 2>/dev/null)" = ACTIVE ]; then
    echo "$n ja existe"; return; fi
  aws ecs create-service --cluster $C --service-name $n --task-definition $P-$n --desired-count $d \
    --capacity-provider-strategy capacityProvider=$P-cp,weight=1 \
    --placement-strategy type=spread,field=attribute:ecs.availability-zone type=binpack,field=memory \
    --deployment-configuration 'minimumHealthyPercent=50,maximumPercent=200,deploymentCircuitBreaker={enable=true,rollback=true}' \
    --enable-ecs-managed-tags --propagate-tags SERVICE --tags key=Project,value=$P \
    "$@" --query 'service.[serviceName,status]' --output text
}
echo "== servicos"
criar commerce-api 2 --service-connect-configuration "{\"enabled\":true,\"namespace\":\"$NS_ARN\",\"services\":[{\"portName\":\"commerce-api\",\"discoveryName\":\"commerce-api\",\"clientAliases\":[{\"port\":8080,\"dnsName\":\"commerce-api.$P.local\"}]}],\"logConfiguration\":$(scl commerce-api)}"
criar order-worker 2
criar chat-api 2 --health-check-grace-period-seconds 60 \
  --load-balancers targetGroupArn=$TG_ARN,containerName=chat-api,containerPort=8080 \
  --service-connect-configuration "{\"enabled\":true,\"namespace\":\"$NS_ARN\",\"logConfiguration\":$(scl chat-api)}"

echo "== esperando os servicos estabilizarem (ate ~10 min)"
aws ecs wait services-stable --cluster $C --services commerce-api order-worker chat-api || echo "ainda nao estavel"
aws ecs describe-services --cluster $C --services commerce-api order-worker chat-api --query 'services[].[serviceName,desiredCount,runningCount,pendingCount,deployments[0].rolloutState]' --output table
source ~/sp.env
curl -s -m 10 http://$ALB_DNS/health; echo
echo "URL: http://$ALB_DNS/"
echo "BLOCO 5 FIM"
