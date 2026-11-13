# Variáveis comuns dos scripts de operação. Uso: source ops/comum.sh [regiao]
# AWS_REGION tem precedência sobre AWS_DEFAULT_REGION na CLI (e o CloudShell já define AWS_REGION): exportar os dois
export MSYS_NO_PATHCONV=1 # no Git Bash do Windows, impede que /ecs/... vire C:/Program Files/Git/ecs/...
export AWS_REGION=${1:-${AWS_REGION:-sa-east-1}} AWS_DEFAULT_REGION=${1:-${AWS_REGION:-sa-east-1}}
export P=setup-pronto C=setup-pronto-cluster ASG=setup-pronto-ecs-asg
export ALB_DNS=$(aws elbv2 describe-load-balancers --names $P-alb --query 'LoadBalancers[0].DNSName' --output text)
# Endereço público: o CloudFront da região (Terraform, edge.tf; o ALB só aceita o CloudFront) ou, sem ele (etapa manual
# de Ohio), o próprio ALB.
CF=$(aws cloudfront list-distributions --query "DistributionList.Items[?Comment=='$P-$AWS_DEFAULT_REGION'].DomainName | [0]" --output text 2>/dev/null)
if [ -n "$CF" ] && [ "$CF" != None ]; then export URL="https://$CF"; else export URL="http://$ALB_DNS"; fi
export Q_URL=$(aws sqs get-queue-url --queue-name $P-orders --query QueueUrl --output text)
export DLQ_URL=$(aws sqs get-queue-url --queue-name $P-orders-dlq --query QueueUrl --output text)
echo "região $AWS_DEFAULT_REGION · $URL/"
