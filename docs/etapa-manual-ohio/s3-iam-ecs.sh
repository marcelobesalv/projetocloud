set -euo pipefail
export AWS_DEFAULT_REGION=us-east-2
P=setup-pronto; ST=~/sp.env; source $ST
save(){ echo "$1=$2" >> $ST; echo "$1=$2"; }
ACC=$(aws sts get-caller-identity --query Account --output text)
trust(){ echo "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Principal\":{\"Service\":\"$1\"},\"Action\":\"sts:AssumeRole\"}]}"; }
role(){ aws iam create-role --role-name $P-$1 --assume-role-policy-document "$(trust $2)" --tags Key=Project,Value=$P --query Role.Arn --output text; }
save ROLE_INSTANCE $(role ecs-instance-role ec2.amazonaws.com)
aws iam attach-role-policy --role-name $P-ecs-instance-role --policy-arn arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role
aws iam attach-role-policy --role-name $P-ecs-instance-role --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
aws iam create-instance-profile --instance-profile-name $P-ecs-instance-role >/dev/null
aws iam add-role-to-instance-profile --instance-profile-name $P-ecs-instance-role --role-name $P-ecs-instance-role
save ROLE_EXEC $(role task-execution-role ecs-tasks.amazonaws.com)
aws iam attach-role-policy --role-name $P-task-execution-role --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
aws iam put-role-policy --role-name $P-task-execution-role --policy-name ler-segredos --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":[\"$SEC_CHAT_API\",\"$SEC_COMMERCE_API\",\"$SEC_ORDER_WORKER\",\"$RDS_SECRET\"]}]}"
save ROLE_CHAT $(role chat-api-task-role ecs-tasks.amazonaws.com)
aws iam put-role-policy --role-name $P-chat-api-task-role --policy-name bedrock-haiku --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"bedrock:InvokeModel\",\"bedrock:InvokeModelWithResponseStream\"],\"Resource\":[\"arn:aws:bedrock:us-east-2:$ACC:inference-profile/us.anthropic.claude-haiku-5-5\",\"arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-5-5\"]}]}"
save ROLE_COMMERCE $(role commerce-api-task-role ecs-tasks.amazonaws.com)
aws iam put-role-policy --role-name $P-commerce-api-task-role --policy-name enviar-pedidos --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"sqs:SendMessage\",\"sqs:GetQueueUrl\",\"sqs:GetQueueAttributes\"],\"Resource\":\"$Q_ARN\"}]}"
save ROLE_WORKER $(role order-worker-task-role ecs-tasks.amazonaws.com)
aws iam put-role-policy --role-name $P-order-worker-task-role --policy-name consumir-pedidos --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"sqs:ReceiveMessage\",\"sqs:DeleteMessage\",\"sqs:ChangeMessageVisibility\",\"sqs:GetQueueUrl\",\"sqs:GetQueueAttributes\"],\"Resource\":\"$Q_ARN\"}]}"
CLUSTER=$(aws ecs create-cluster --cluster-name $P-cluster --tags key=Project,value=$P --query cluster.clusterArn --output text); save CLUSTER $CLUSTER
echo "aguardando propagacao do IAM..."; sleep 15
AMI=$(aws ssm get-parameter --name /aws/service/ecs/optimized-ami/amazon-linux-2023/recommended/image_id --query Parameter.Value --output text); save AMI $AMI
UD=$(printf '#!/bin/bash\necho ECS_CLUSTER=%s-cluster >> /etc/ecs/ecs.config\necho ECS_ENABLE_TASK_IAM_ROLE=true >> /etc/ecs/ecs.config\necho ECS_ENABLE_TASK_IAM_ROLE_NETWORK_HOST=true >> /etc/ecs/ecs.config\n' $P | base64 -w0)
cat > /tmp/lt.json <<J
{"ImageId":"$AMI","InstanceType":"t3.medium","IamInstanceProfile":{"Name":"$P-ecs-instance-role"},"SecurityGroupIds":["$ECS_SG"],"MetadataOptions":{"HttpTokens":"required","HttpEndpoint":"enabled","HttpPutResponseHopLimit":2},"UserData":"$UD","TagSpecifications":[{"ResourceType":"instance","Tags":[{"Key":"Name","Value":"$P-ecs"},{"Key":"Project","Value":"$P"}]}]}
J
LT=$(aws ec2 create-launch-template --launch-template-name $P-ecs-lt --launch-template-data file:///tmp/lt.json --tag-specifications "ResourceType=launch-template,Tags=[{Key=Project,Value=$P}]" --query LaunchTemplate.LaunchTemplateId --output text); save LT $LT
aws autoscaling create-auto-scaling-group --auto-scaling-group-name $P-ecs-asg --launch-template "LaunchTemplateId=$LT,Version=\$Latest" --min-size 2 --max-size 4 --desired-capacity 2 --vpc-zone-identifier "$APP1,$APP2" --new-instances-protected-from-scale-in --tags "Key=Project,Value=$P,PropagateAtLaunch=true"
ASG_ARN=$(aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names $P-ecs-asg --query 'AutoScalingGroups[0].AutoScalingGroupARN' --output text); save ASG_ARN $ASG_ARN
aws ecs create-capacity-provider --name $P-cp --auto-scaling-group-provider "autoScalingGroupArn=$ASG_ARN,managedScaling={status=ENABLED,targetCapacity=90},managedTerminationProtection=ENABLED" --tags key=Project,value=$P >/dev/null
aws ecs put-cluster-capacity-providers --cluster $P-cluster --capacity-providers $P-cp --default-capacity-provider-strategy capacityProvider=$P-cp,weight=1 >/dev/null
echo "BLOCO 3 OK"
