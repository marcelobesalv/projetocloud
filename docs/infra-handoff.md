# Handoff de infraestrutura — Setup Pronto

Alvo: **ECS sobre EC2** (network mode `awsvpc` recomendado: um security group por serviço), **ALB** público só
para o chat-api, **RDS PostgreSQL 16**, **SQS** (`orders` + `orders-dlq`), **Bedrock** (Claude Haiku 5.5),
**CloudWatch Logs** (driver `awslogs`).

Imagens (ECR, publicadas por `push.sh`/`push.ps1` com a tag = commit curto):

| Imagem | Origem |
|---|---|
| `setup-pronto/chat-api` | `services/chat-api` (target `runtime`) |
| `setup-pronto/commerce-api` | `services/commerce-api` (target `runtime`) |
| `setup-pronto/order-worker` | `services/order-worker` (target `runtime`) |
| `setup-pronto/db-migrate` | `db/` |

Todas: Python 3.12 slim, usuário **não-root 10001**, entrypoint `python -m app` (migrate: `python bootstrap.py`),
nada gravado em disco → pode ligar `readonlyRootFilesystem: true`. Logs em JSON no stdout.

## Comum a todos os serviços

| Item | Valor |
|---|---|
| Porta do container | **8080** (variável `PORT`) |
| Health check (task definition) | `["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)\""]`, interval 15 s, timeout 5 s, retries 3, startPeriod 30 s |
| `/health` | 200 `{"status":"ok"}` ou 503 `{"status":"erro", ...}` |
| Encerramento | trata SIGTERM com graceful shutdown de até 25 s → `stopTimeout` ≥ 30 s |

O `HEALTHCHECK` do Dockerfile **não** é usado pelo ECS — repita-o na task definition.

### Variáveis de banco (todos os serviços)

| Variável | Segredo? | Valor |
|---|---|---|
| `DB_HOST` | não | endpoint do RDS |
| `DB_PORT` | não | `5432` |
| `DB_NAME` | não | `setup_pronto` |
| `DB_USER` | não | usuário do serviço (abaixo) |
| `DB_PASSWORD` | **sim** | Secrets Manager / SSM SecureString via `secrets` da task definition |
| `DB_SSLMODE` | não | `require` |
| `DB_POOL_SIZE` | não | opcional (5; worker 3) |
| `DB_STATEMENT_TIMEOUT_MS` | não | opcional (5000; worker 10000) |
| `LOG_LEVEL` | não | `INFO` |

---

## chat-api (público, atrás do ALB)

| Item | Valor |
|---|---|
| Porta | 8080 → target group HTTP, health check path **`/health`**, matcher 200 |
| ALB | idle timeout ≥ 60 s (o turno do chat tem orçamento de 50 s) |
| CPU / memória sugeridas | 256 CPU units / `memoryReservation` 256 MiB, `memory` 512 MiB; 2 tasks |
| Escala | por `RequestCountPerTarget` ou CPU; é I/O-bound (espera o LLM) |
| Usuário de banco | `chat_api` (SELECT/INSERT/UPDATE em `conversas`) |

| Variável | Segredo? | Valor |
|---|---|---|
| `DB_*` | `DB_PASSWORD` sim | `DB_USER=chat_api` |
| `COMMERCE_API_URL` | não | URL interna, ex.: `http://commerce-api.setup-pronto.local:8080` (Service Connect/Cloud Map) ou o DNS de um ALB interno |
| `COMMERCE_TIMEOUT_SECONDS` | não | `5` |
| `LLM_PROVIDER` | não | `bedrock` |
| `LLM_MODEL_ID` | não | us-east-2: `us.anthropic.claude-haiku-5-5` · sa-east-1: `global.anthropic.claude-haiku-5-5` |
| `AWS_REGION` | não | `us-east-2` ou `sa-east-1` |
| `LLM_TIMEOUT_SECONDS`, `LLM_MAX_TOKENS`, `MAX_TOOL_ITERATIONS`, `CHAT_TURN_BUDGET_SECONDS`, `HISTORY_MAX_MESSAGES` | não | opcionais (30, 1024, 6, 50, 40) |

Não defina `AWS_PROFILE` nem chaves de acesso: as credenciais vêm da task role.

**Task role** (`bedrock:InvokeModel` cobre a API Converse):
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "bedrock:InvokeModel",
    "Resource": [
      "arn:aws:bedrock:<regiao>:<conta>:inference-profile/<LLM_MODEL_ID>",
      "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-5-5*"
    ]
  }]
}
```
Para o perfil `global.*` (sa-east-1), confirme na documentação de cross-region inference do Bedrock quais ARNs de
foundation model a política precisa cobrir. O acesso ao modelo precisa estar habilitado na conta.

**Security group**: entrada 8080 só do SG do ALB; saída 8080 para o SG do commerce-api, 5432 para o RDS,
443 para o Bedrock (NAT gateway ou VPC endpoint `bedrock-runtime`).

---

## commerce-api (interno)

| Item | Valor |
|---|---|
| Porta | 8080, **sem** exposição pública; Service Connect ou ALB interno (health check `/health`) |
| CPU / memória sugeridas | 256 CPU / `memoryReservation` 256 MiB, `memory` 512 MiB; 2 tasks |
| Usuário de banco | `commerce_api` (leitura de `produtos`/`estoque`; escrita em `pedidos`, `itens_pedido`, `outbox`) |

| Variável | Segredo? | Valor |
|---|---|---|
| `DB_*` | `DB_PASSWORD` sim | `DB_USER=commerce_api` |
| `SQS_QUEUE_URL` | não | URL da fila `orders` |
| `AWS_REGION` | não | região da fila |
| `OUTBOX_PUBLISHER_ENABLED` | não | `true` (seguro com várias tasks: `SKIP LOCKED`) |
| `OUTBOX_POLL_INTERVAL_SECONDS`, `OUTBOX_BATCH_SIZE`, `KIT_DISCOUNT_TIERS`, `MAX_QTD_POR_ITEM` | não | opcionais (1.0, 10, `2:5,3:8,4:10,5:12`, 10) |

Não use `SQS_ENDPOINT_URL` / `SQS_QUEUE_NAME` na AWS (são do ambiente local).

**Task role**:
```json
{ "Effect": "Allow", "Action": "sqs:SendMessage",
  "Resource": "arn:aws:sqs:<regiao>:<conta>:orders" }
```
(Se a fila usar KMS com chave gerenciada pelo cliente, adicione `kms:GenerateDataKey` e `kms:Decrypt` nela.)

**Security group**: entrada 8080 só do SG do chat-api (e do ALB interno, se houver); saída 5432 para o RDS e 443
para o SQS (NAT ou VPC endpoint `sqs`).

---

## order-worker (sem tráfego de entrada)

| Item | Valor |
|---|---|
| Porta | 8080 só para o health check do container; **não registrar em target group** |
| `/health` | 503 se o banco cair **ou** se o loop do consumidor parar por mais de `HEALTH_MAX_LOOP_AGE_SECONDS` (o ECS reinicia a task) |
| CPU / memória sugeridas | 256 CPU / `memoryReservation` 192 MiB, `memory` 384 MiB; 1–2 tasks |
| Escala | opcional: Target Tracking em `ApproximateNumberOfMessagesVisible` da fila |
| Usuário de banco | `order_worker` (leitura de `produtos`, `itens_pedido`; SELECT/UPDATE em `pedidos`, `estoque`) |

| Variável | Segredo? | Valor |
|---|---|---|
| `DB_*` | `DB_PASSWORD` sim | `DB_USER=order_worker`, `DB_STATEMENT_TIMEOUT_MS=10000` |
| `SQS_QUEUE_URL` | não | URL da fila `orders` (obrigatória) |
| `AWS_REGION` | não | região da fila |
| `SQS_WAIT_TIME_SECONDS`, `SQS_MAX_MESSAGES`, `HEALTH_MAX_LOOP_AGE_SECONDS` | não | opcionais (20, 10, 90) |

**Task role**:
```json
{ "Effect": "Allow",
  "Action": ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes"],
  "Resource": "arn:aws:sqs:<regiao>:<conta>:orders" }
```

**Security group**: sem entrada; saída 5432 para o RDS e 443 para o SQS.

---

## SQS

| Fila | Configuração |
|---|---|
| `orders` | Standard; visibility timeout **60 s**; receive wait time **20 s**; retenção 4 dias; redrive policy → `orders-dlq`, **`maxReceiveCount=5`** |
| `orders-dlq` | Standard; retenção 14 dias; alarme em `ApproximateNumberOfMessagesVisible > 0` |

## RDS

PostgreSQL 16, `rds.force_ssl=1`, sem acesso público. Entrada 5432 só dos SGs do chat-api, commerce-api,
order-worker e do job de migração. A senha master fica no Secrets Manager (pode ser o segredo gerenciado pelo RDS).
O banco `setup_pronto` precisa existir (crie com `DBName` ao criar a instância).

## Execution role (comum às task definitions)

`AmazonECSTaskExecutionRolePolicy` (pull no ECR + CloudWatch Logs) **mais** `secretsmanager:GetSecretValue` nos
segredos usados (e `kms:Decrypt` se o segredo usar CMK; `ssm:GetParameters` se usar SSM).

Logs: driver `awslogs`, um log group por serviço (`/ecs/setup-pronto/<serviço>`). Metric filters sugeridos estão
no README (latência e falhas do LLM, commerce indisponível, pedidos rejeitados, mensagens problemáticas).

---

## Job de migração (tarefa avulsa no ECS)

Task definition `setup-pronto-migrate`, imagem `setup-pronto/db-migrate:<tag>`, 256 CPU / 256 MiB, sem porta,
sem health check, mesmo VPC/subnets privadas e um SG com saída 5432 para o RDS. Não é um *service*: roda e termina.

| Variável | Segredo? | Valor |
|---|---|---|
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_SSLMODE` | não | como acima (`DB_SSLMODE=require`) |
| `DB_USER` | não | usuário master (dono do schema) |
| `DB_PASSWORD` | **sim** | senha master (`secrets`; se for o segredo gerenciado do RDS, use `"<arn>:password::"`) |
| `CHAT_DB_PASSWORD` | **sim** | senha do `chat_api` (mesmo segredo que o chat-api usa como `DB_PASSWORD`) |
| `COMMERCE_DB_PASSWORD` | **sim** | senha do `commerce_api` |
| `WORKER_DB_PASSWORD` | **sim** | senha do `order_worker` |
| `CHAT_DB_USER`, `COMMERCE_DB_USER`, `WORKER_DB_USER` | não | opcionais (padrões `chat_api`, `commerce_api`, `order_worker`) |
| `SEED_ON_MIGRATE` | não | `true` (o seed é idempotente e nunca sobrescreve estoque) |

O job: cria/atualiza os 3 usuários com as senhas recebidas → `alembic upgrade head` → GRANTs mínimos → seed.
É idempotente: rode **a cada deploy, antes** de atualizar os serviços. Trocar a senha de um serviço = atualizar o
segredo e rodar o job de novo (ele faz `ALTER ROLE`), depois forçar novo deploy do serviço.

```bash
REGIAO=us-east-2
CLUSTER=setup-pronto
SUBNETS=subnet-aaa,subnet-bbb          # privadas
SG=sg-migrate

TASK_ARN=$(aws ecs run-task --region $REGIAO --cluster $CLUSTER \
  --task-definition setup-pronto-migrate \
  --capacity-provider-strategy capacityProvider=<seu-capacity-provider-ec2>,weight=1 \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=DISABLED}" \
  --query 'tasks[0].taskArn' --output text)

aws ecs wait tasks-stopped --region $REGIAO --cluster $CLUSTER --tasks $TASK_ARN

aws ecs describe-tasks --region $REGIAO --cluster $CLUSTER --tasks $TASK_ARN \
  --query 'tasks[0].containers[0].[exitCode,reason]' --output text   # 0 = sucesso
```
(Sem capacity provider, use `--launch-type EC2`.) Logs do job: log group `/ecs/setup-pronto/migrate`, um JSON por
linha (`usuário … criado`, `migrações aplicadas`, `seed: {...}`). Se o código de saída não for 0, não atualize os
serviços.

## Ordem de um deploy
1. `./push.sh <regiao> <conta>` → imagens com a tag do commit.
2. Nova revisão das 4 task definitions apontando para a tag.
3. Rodar o job de migração e conferir `exitCode = 0`.
4. `aws ecs update-service --force-new-deployment` (ou nova revisão) em commerce-api, order-worker e chat-api.
