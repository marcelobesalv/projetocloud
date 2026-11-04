set -euo pipefail
export AWS_DEFAULT_REGION=us-east-2
P=setup-pronto; ST=~/sp.env; source $ST
save(){ echo "$1=$2" >> $ST; echo "$1=$2"; }
sg(){ aws ec2 create-security-group --group-name $P-$1 --description "$2" --vpc-id $VPC --tag-specifications "ResourceType=security-group,Tags=[{Key=Name,Value=$P-$1},{Key=Project,Value=$P}]" --query GroupId --output text; }
ALB_SG=$(sg alb-sg "ALB publico"); save ALB_SG $ALB_SG
ECS_SG=$(sg ecs-instances-sg "Instancias ECS"); save ECS_SG $ECS_SG
RDS_SG=$(sg rds-sg "RDS PostgreSQL"); save RDS_SG $RDS_SG
aws ec2 authorize-security-group-ingress --group-id $ALB_SG --ip-permissions 'IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=0.0.0.0/0}]' 'IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0}]' >/dev/null
aws ec2 authorize-security-group-ingress --group-id $ECS_SG --ip-permissions "IpProtocol=tcp,FromPort=32768,ToPort=65535,UserIdGroupPairs=[{GroupId=$ALB_SG}]" "IpProtocol=-1,UserIdGroupPairs=[{GroupId=$ECS_SG}]" >/dev/null
aws ec2 authorize-security-group-ingress --group-id $RDS_SG --ip-permissions "IpProtocol=tcp,FromPort=5432,ToPort=5432,UserIdGroupPairs=[{GroupId=$ECS_SG}]" >/dev/null
DLQ_URL=$(aws sqs create-queue --queue-name $P-orders-dlq --attributes MessageRetentionPeriod=1209600 --tags Project=$P --query QueueUrl --output text); save DLQ_URL $DLQ_URL
DLQ_ARN=$(aws sqs get-queue-attributes --queue-url $DLQ_URL --attribute-names QueueArn --query Attributes.QueueArn --output text); save DLQ_ARN $DLQ_ARN
cat > /tmp/q.json <<J
{"VisibilityTimeout":"60","ReceiveMessageWaitTimeSeconds":"20","RedrivePolicy":"{\"deadLetterTargetArn\":\"$DLQ_ARN\",\"maxReceiveCount\":\"5\"}"}
J
Q_URL=$(aws sqs create-queue --queue-name $P-orders --attributes file:///tmp/q.json --tags Project=$P --query QueueUrl --output text); save Q_URL $Q_URL
Q_ARN=$(aws sqs get-queue-attributes --queue-url $Q_URL --attribute-names QueueArn --query Attributes.QueueArn --output text); save Q_ARN $Q_ARN
for n in chat-api commerce-api order-worker db-migrate; do aws ecr create-repository --repository-name $P/$n --image-scanning-configuration scanOnPush=true --image-tag-mutability IMMUTABLE --tags Key=Project,Value=$P --query repository.repositoryUri --output text; done
for n in chat-api commerce-api order-worker migrate; do aws logs create-log-group --log-group-name /ecs/$P/$n --tags Project=$P; aws logs put-retention-policy --log-group-name /ecs/$P/$n --retention-in-days 14; done
for u in chat_api commerce_api order_worker; do A=$(aws secretsmanager create-secret --name $P/db/$u --secret-string "$(openssl rand -hex 24)" --tags Key=Project,Value=$P --query ARN --output text); save SEC_${u^^} $A; done
aws rds create-db-subnet-group --db-subnet-group-name $P-db-subnets --db-subnet-group-description "Sub-redes de dados" --subnet-ids $DATA1 $DATA2 --tags Key=Project,Value=$P >/dev/null
RDS_SECRET=$(aws rds create-db-instance --db-instance-identifier $P-db --engine postgres --engine-version 16 --db-instance-class db.t4g.micro --allocated-storage 20 --storage-type gp3 --master-username pgadmin --manage-master-user-password --db-name setuppronto --vpc-security-group-ids $RDS_SG --db-subnet-group-name $P-db-subnets --no-publicly-accessible --no-multi-az --backup-retention-period 1 --tags Key=Project,Value=$P --query DBInstance.MasterUserSecret.SecretArn --output text); save RDS_SECRET $RDS_SECRET
echo "BLOCO 2 OK (RDS criando em segundo plano)"
