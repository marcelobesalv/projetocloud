# Infraestrutura como código — Setup Pronto

Terraform que reproduz, em qualquer região, a montagem feita à mão em Ohio (us-east-2): VPC de 3 camadas,
ECS sobre EC2 (ASG + capacity provider), ALB, RDS PostgreSQL, SQS + DLQ, Secrets Manager, IAM de menor
privilégio, Service Connect, auto scaling das tasks, metric filters, alarmes e dashboard.
Alvo padrão: **São Paulo (sa-east-1)**, com o Bedrock pelo perfil `global.anthropic.claude-haiku-5-5`.

## Arquivos
| Arquivo | Conteúdo |
|---|---|
| `network.tf` | VPC, sub-redes pública/aplicação/dados em 2 AZs, IGW, 1 NAT, endpoint S3 |
| `security.tf` | SGs: ALB (80 da internet) → instâncias (portas dinâmicas) → RDS (5432) |
| `data.tf` | SQS + DLQ (5 tentativas), ECR (imutável, scan), log groups, segredos dos usuários de banco, RDS |
| `iam.tf` | instance role, execution role (só os 4 segredos), task roles: chat (só o perfil do Bedrock), commerce (SendMessage), worker (consumir) |
| `ecs.tf` | namespace do Service Connect, cluster, launch template (AMI ECS AL2023), ASG, capacity provider |
| `alb.tf` | ALB (idle 60 s), target group `/health`, listener 80 |
| `app.tf` | **fase 2**: task definitions, migração (`scripts/run-migrate.sh`), 3 serviços, auto scaling |
| `observability.tf` | 13 metric filters, 9 alarmes → SNS, dashboard |

## Como aplicar (CloudShell de sa-east-1)

```bash
# 0) Terraform no CloudShell (uma vez)
curl -sLo /tmp/tf.zip https://releases.hashicorp.com/terraform/1.9.8/terraform_1.9.8_linux_amd64.zip
mkdir -p ~/bin && unzip -o /tmp/tf.zip -d ~/bin
export PATH=~/bin:$PATH TF_DATA_DIR=/tmp/tfdata   # o home do CloudShell tem só 1 GB; o provider fica em /tmp

cd infra/terraform
terraform init

# 1) Fase 1: rede, dados, ECR, IAM, cluster, ALB, observabilidade (~10 min, o RDS é o mais lento)
terraform apply

# 2) Imagens no ECR da região: rebuild na máquina de build...
#      ./push.sh sa-east-1 <conta>
#    ...ou cópia das imagens já publicadas em Ohio (sem rebuild):
#      scripts/copiar-imagens.sh us-east-2 sa-east-1 <tag>

# 3) Fase 2: task definitions → migração (run-task, falha o apply se exitCode ≠ 0) → serviços → auto scaling
terraform apply -var deploy_app=true -var image_tag=<tag>
terraform output url
```

Nova versão: publique a imagem com a nova tag e repita o passo 3 com ela. A migração roda de novo (nova revisão
da task definition) antes de os serviços trocarem de revisão; o circuit breaker desfaz um deploy que não estabiliza.

## Decisões
- **Duas fases** (`deploy_app`): o ECR precisa existir antes do push, e os serviços só sobem depois da migração
  (sem ela, o `/health` falha porque os usuários de banco não existem e o circuit breaker marcaria o deploy como falho).
- **Rede `bridge`** com portas dinâmicas: t3.medium tem poucas ENIs, então `awsvpc` limitaria a ~2 tasks por instância.
- **Bedrock global**: a política do chat tem as 3 declarações da documentação de *global cross-Region inference*
  (perfil na região, modelo na região e o ARN de modelo sem região com `aws:RequestedRegion = unspecified`).
  Com um perfil `us.`/`eu.` o Terraform gera a política geográfica.
- **Senhas** dos usuários de serviço geradas pelo provider `random` e guardadas no Secrets Manager; a senha master é
  gerenciada pelo RDS. Ficam no state: mantenha o `terraform.tfstate` fora do git (há `.gitignore`).
- **Nomes de IAM com a região** (`setup-pronto-sa-east-1-...`): IAM é global na conta e os papéis de Ohio já existem.
- **State local** (`terraform.tfstate` no home do CloudShell, que persiste). Para trabalho em grupo, um backend S3.
- **Custo**: 1 NAT, RDS Single-AZ, Container Insights desligado. `terraform destroy` remove tudo (ECR com `force_delete`,
  RDS sem snapshot final, segredos sem janela de recuperação).
