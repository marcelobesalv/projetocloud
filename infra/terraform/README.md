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
| `build.tf` | build das imagens dentro do apply: código no S3 → CodeBuild → ECR da região (`scripts/build-imagens.sh`) |
| `app.tf` | task definitions, migração (`scripts/run-migrate.sh`), 3 serviços, auto scaling |
| `observability.tf` | 15 metric filters, alarmes → SNS (fila, DLQ, pedidos travados, ALB, LLM, RDS, tasks abaixo do mínimo, reserva do cluster, capacidade do ASG), dashboard |
| `audit.tf` | CloudTrail multirregião (S3 privado com validação de integridade + CloudWatch Logs), 6 métricas e 5 alarmes de segurança (padrões CIS), 7 consultas salvas do Logs Insights; o que fazer em cada alarme: `docs/runbook.md` |
| `backend.tf`, `envs/`, `bootstrap/` | state remoto no S3 e valores por região |

## Como aplicar (CloudShell de sa-east-1)

```bash
# 0) Terraform >= 1.11 no CloudShell (uma vez por sessão do CloudShell)
curl -sLo /tmp/tf.zip https://releases.hashicorp.com/terraform/1.11.4/terraform_1.11.4_linux_amd64.zip
mkdir -p ~/bin && unzip -o /tmp/tf.zip -d ~/bin
export PATH=~/bin:$PATH TF_DATA_DIR=/tmp/tfdata   # o home do CloudShell tem só 1 GB; o provider fica em /tmp

# 1) Bucket do state remoto (uma vez por conta/região; state local só deste módulo)
(cd bootstrap && terraform init && terraform apply -var region=sa-east-1)

# 2) O ambiente inteiro, num único apply (~10 min): rede, RDS, ECR, IAM, cluster, ALB, borda, auditoria e
#    observabilidade; o CodeBuild constrói e publica as imagens a partir do código (build.tf); depois a migração
#    (run-task; o apply falha se exitCode ≠ 0), os serviços e o auto scaling.
terraform init -backend-config=envs/sa-east-1.backend.hcl
terraform apply -var-file=envs/sa-east-1.tfvars
terraform output url

# Remover tudo
terraform destroy -var-file=envs/sa-east-1.tfvars
```

Nova versão: commit e `terraform apply` de novo. A tag das imagens é um hash do código: mudou o código, o CodeBuild
constrói a tag nova, a migração roda (nova revisão da task definition) e os serviços trocam de revisão; o circuit
breaker desfaz um deploy que não estabiliza. Código igual não gera build nem deploy.
Para usar imagens publicadas de outro jeito (`push.sh`), passe `-var image_tag=<tag>` (o build não roda).
`-var deploy_app=false` cria só a infraestrutura, sem a aplicação.
Trocar as senhas dos usuários de banco: `-var db_password_version=2` (grava novas senhas, roda a migração, que faz
`ALTER ROLE`, e força novas tasks).

O bucket do state é o único pré-requisito fora deste módulo: o backend precisa existir antes do `terraform init`.

Outra região: copie `envs/sa-east-1.*` para a nova região (bucket do bootstrap, `region`, `vpc_cidr`, perfil do
Bedrock disponível lá). AMI (parâmetro SSM), zonas (data source), endpoints, ARNs e imagens (construídas no ECR da
própria região) são obtidos ali; nenhum recurso fica fora da região do ambiente.

## Decisões
- **Um apply só, imagens construídas na AWS** (`build.tf`): o código vai para um bucket S3 da região e um CodeBuild
  (Docker privilegiado, role que só lê o bucket e publica nos 4 repositórios) faz build e push no ECR da própria
  região; o apply espera o build. A ordem ECR → imagens → migração → serviços vem das dependências: sem a migração,
  o `/health` falharia (os usuários de banco não existiriam) e o circuit breaker marcaria o deploy como falho.
  Imagem base pelo espelho `public.ecr.aws/docker/library` (o Docker Hub limita downloads anônimos do CodeBuild).
  Testado do zero em 13/11: 173 recursos em 9 min (`docs/resultados/recriacao-sp.md`).
- **Propagação do IAM**: CodeBuild e migração rodam segundos depois de as roles serem criadas; os scripts repetem
  apenas a falha "not authorized" da fila/início, com espera.
- **Entrada pelo ALB em HTTP** (`alb.tf`, `security.tf`): o ALB nas sub-redes públicas é o único ponto de entrada
  (SG com a porta 80 aberta à internet); instâncias e banco ficam em sub-redes privadas. Sem domínio próprio não há
  certificado no ACM, então não há HTTPS. Até 18/11 havia CloudFront + WAF na frente (`edge.tf`, removido para
  ficar no escopo do enunciado).
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
