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
| `observability.tf` | 15 metric filters, alarmes → SNS (fila, DLQ, pedidos travados, ALB, LLM, RDS, tasks abaixo do mínimo, reserva do cluster, capacidade do ASG), dashboard |
| `backend.tf`, `envs/`, `bootstrap/` | state remoto no S3 e valores por região |

## Como aplicar (CloudShell de sa-east-1)

```bash
# 0) Terraform >= 1.11 no CloudShell (uma vez por sessão do CloudShell)
curl -sLo /tmp/tf.zip https://releases.hashicorp.com/terraform/1.11.4/terraform_1.11.4_linux_amd64.zip
mkdir -p ~/bin && unzip -o /tmp/tf.zip -d ~/bin
export PATH=~/bin:$PATH TF_DATA_DIR=/tmp/tfdata   # o home do CloudShell tem só 1 GB; o provider fica em /tmp

# 1) Bucket do state remoto (uma vez por conta/região; state local só deste módulo)
(cd bootstrap && terraform init && terraform apply -var region=sa-east-1)

# 2) Fase 1: rede, dados, ECR, IAM, cluster, ALB, observabilidade (~10 min, o RDS é o mais lento)
terraform init -backend-config=envs/sa-east-1.backend.hcl
terraform apply -var-file=envs/sa-east-1.tfvars

# 3) Imagens no ECR de São Paulo, a partir do código (na máquina de build, com Docker):
#      ./push.sh sa-east-1 <conta>          (ou .\push.ps1 sa-east-1 <conta>)
#    Alternativa sem rebuild, copiando de outra região: scripts/copiar-imagens.sh us-east-2 sa-east-1 <tag>

# 4) Fase 2: task definitions → migração (run-task; o apply falha se exitCode ≠ 0) → serviços → auto scaling
terraform apply -var-file=envs/sa-east-1.tfvars -var deploy_app=true -var image_tag=<tag>
terraform output url

# Remover tudo
terraform destroy -var-file=envs/sa-east-1.tfvars -var deploy_app=true -var image_tag=<tag>
```

Nova versão: publique a imagem com a nova tag e repita o passo 4 com ela. A migração roda de novo (nova revisão
da task definition) antes de os serviços trocarem de revisão; o circuit breaker desfaz um deploy que não estabiliza.
Trocar as senhas dos usuários de banco: `-var db_password_version=2` (grava novas senhas, roda a migração, que faz
`ALTER ROLE`, e força novas tasks).

Outra região: copie `envs/sa-east-1.*` para a nova região (bucket do bootstrap, `region`, `vpc_cidr`, perfil do
Bedrock disponível lá). AMI (parâmetro SSM), zonas (data source), endpoints e ARNs são obtidos na própria região.

## Decisões
- **Duas fases** (`deploy_app`): o ECR precisa existir antes do push, e os serviços só sobem depois da migração
  (sem ela, o `/health` falha porque os usuários de banco não existem e o circuit breaker marcaria o deploy como falho).
- **Rede `bridge`** com portas dinâmicas: t3.medium tem poucas ENIs, então `awsvpc` limitaria a ~2 tasks por instância.
- **Bedrock global**: a política do chat tem as 3 declarações da documentação de *global cross-Region inference*
  (perfil na região, modelo na região e o ARN de modelo sem região com `aws:RequestedRegion = unspecified`).
  Com um perfil `us.`/`eu.` o Terraform gera a política geográfica.
- **Senhas fora do state**: as dos usuários de serviço são geradas por um `ephemeral "random_password"` e gravadas no
  Secrets Manager por argumento write-only (`secret_string_wo`), por isso o Terraform mínimo é 1.11. A senha master é
  gerenciada pelo próprio RDS (`manage_master_user_password`). Nenhum segredo aparece nos `.tf`, no state ou nas imagens.
- **Nomes de IAM com a região** (`setup-pronto-sa-east-1-...`): IAM é global na conta e os papéis de Ohio já existem.
- **State remoto** no S3 (`backend.tf` + `envs/<regiao>.backend.hcl`): criptografado com KMS, versionado, só TLS,
  sem acesso público e com trava por lockfile no próprio bucket (`use_lockfile`), sem tabela DynamoDB.
- **Custo**: 1 NAT, RDS Single-AZ, Container Insights desligado. `terraform destroy` remove tudo (ECR com `force_delete`,
  RDS sem snapshot final, segredos sem janela de recuperação).
