# Variáveis comuns dos scripts de operação. Uso: source ops/comum.sh [regiao]
# AWS_REGION tem precedência sobre AWS_DEFAULT_REGION na CLI (e o CloudShell já define AWS_REGION): exportar os dois
export MSYS_NO_PATHCONV=1 # no Git Bash do Windows, impede que /ecs/... vire C:/Program Files/Git/ecs/...
export AWS_REGION=${1:-${AWS_REGION:-sa-east-1}} AWS_DEFAULT_REGION=${1:-${AWS_REGION:-sa-east-1}}
export P=setup-pronto C=setup-pronto-cluster ASG=setup-pronto-ecs-asg
export ALB_DNS=$(aws elbv2 describe-load-balancers --names $P-alb --query 'LoadBalancers[0].DNSName' --output text)
export Q_URL=$(aws sqs get-queue-url --queue-name $P-orders --query QueueUrl --output text)
export DLQ_URL=$(aws sqs get-queue-url --queue-name $P-orders-dlq --query QueueUrl --output text)
echo "região $AWS_DEFAULT_REGION · http://$ALB_DNS/"
