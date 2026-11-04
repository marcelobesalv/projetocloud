# Etapa manual — Ohio (us-east-2)

Montada à mão com a AWS CLI no CloudShell de us-east-2, sem ferramenta de IaC, em 03/11/2026.
Conta `202757356229`. Tudo tem a tag `Project=setup-pronto`. Os scripts gravam os IDs criados em `~/sp.env`,
no home do CloudShell de Ohio, e os scripts seguintes leem de lá.

| Ordem | Script | O que cria |
|---|---|---|
| 1 | `s1-rede.sh` | VPC 10.20.0.0/16; sub-redes pública, de aplicação e de dados em 2 AZs; IGW; 1 NAT; route tables; endpoint S3 |
| 2 | `s2-base.sh` | SGs (ALB → instâncias → RDS), SQS `orders` + DLQ, 4 repositórios ECR, log groups, segredos dos usuários de banco, RDS PostgreSQL 16 |
| 3 | `s3-iam-ecs.sh` | 5 roles, cluster ECS, launch template (AMI ECS AL2023 pelo SSM), ASG 2/2/4 t3.medium, capacity provider |
| — | `../../push.sh us-east-2 <conta>` | Imagens no ECR (na máquina de build) |
| 4 | `s4-tasks.sh` | Task definitions (rede bridge, porta dinâmica) e o job de migração (`run-task`), conferindo o exitCode |
| 5 | `s5-servicos.sh` | Namespace do Service Connect, ALB + target group + listener, os 3 serviços |
| 6 | `s6-obs.py` | Auto scaling das tasks, 13 metric filters, alarmes, SNS, dashboard |
| 7 | `../../ops/ohio-alarmes-extra.sh` | Container Insights, métricas do ASG e alarmes de tasks, reserva do cluster e capacidade |

Os scripts 1 a 3 exportam só `AWS_DEFAULT_REGION`. Rode-os no CloudShell **de Ohio**: o CloudShell define
`AWS_REGION`, que tem precedência. A tag padrão do `s4-tasks.sh` é `37791f6`; para outra, use `TAG=<tag> bash s4-tasks.sh`.

## Estado atual
- Imagem em uso: `37791f6`. A `c44c936` (com o botão "Tentar de novo") já está no ECR de Ohio; para trocar, rode
  `TAG=c44c936 bash s4-tasks.sh` (novas revisões e migração) e um `aws ecs update-service --force-new-deployment` em cada serviço.
- Pausado: tasks em 0, ASG com mínimo 0, RDS parado. Para religar: `ops/retomar.sh us-east-2`. Para pausar: `ops/pausar.sh us-east-2`.
  Pausado, ainda cobram o NAT e o ALB. O RDS parado religa sozinho depois de 7 dias.
- Alarmes que disparam com o ambiente pausado (`asg-abaixo-do-minimo`, `*-tasks-abaixo-do-minimo`) são esperados.
- A DLQ tem 1 mensagem do teste de mensagem inválida: `aws sqs purge-queue --queue-url <url da DLQ>` antes da apresentação.

## Recursos principais
| Recurso | Nome / ID |
|---|---|
| VPC | `vpc-0227b82c8f3d9d45b` |
| Cluster / capacity provider / ASG | `setup-pronto-cluster` / `setup-pronto-cp` / `setup-pronto-ecs-asg` |
| ALB | `setup-pronto-alb` (`http://setup-pronto-alb-562905076.us-east-2.elb.amazonaws.com/`) |
| RDS | `setup-pronto-db` (banco `setuppronto`, master `pgadmin` com senha gerenciada pelo RDS) |
| Filas | `setup-pronto-orders`, `setup-pronto-orders-dlq` |
| Segredos | `setup-pronto/db/{chat_api,commerce_api,order_worker}` |
| Roles | `setup-pronto-{ecs-instance,task-execution,chat-api-task,commerce-api-task,order-worker-task}-role` |
| Observabilidade | log groups `/ecs/setup-pronto/*`, namespace `SetupPronto`, dashboard e SNS `setup-pronto-alertas` |

## Testes feitos em Ohio (03/11)
Carga de 10 min a ~30 req/s, com 0 erros. O chat-api escalou de 2 para 6 tasks e o ASG de 3 para 4 instâncias.
Uma task parada foi substituída sem erro. A mensagem inválida foi para a DLQ e disparou o alarme. Com o commerce
desligado, o chat deu a resposta fixa e o alarme disparou. O resumo de todos os testes está no doc do roteiro da apresentação.
