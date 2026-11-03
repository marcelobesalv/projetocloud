# Setup Pronto

Loja de periféricos com um consultor de IA que conversa em português, verifica a compatibilidade com o
equipamento do cliente, monta um kit com desconto progressivo e registra o pedido. A IA usa **ferramentas
reais** (tool use) para consultar catálogo, estoque e pedidos — nunca inventa produto, preço ou estoque.

```
                  ┌────────────┐  HTTP + X-Correlation-ID   ┌──────────────┐
 navegador ──────▶│  chat-api  │───────────────────────────▶│ commerce-api │──┐ outbox (mesma transação)
   GET / POST     │ (tool use) │                            │  (interno)   │  │ publicador em background
   /chat          └─────┬──────┘                            └──────┬───────┘  ▼
                        │ Converse (Bedrock) ou Fake               │       ┌───────────┐   DLQ orders-dlq
                        ▼                                          │       │ SQS orders│── (maxReceiveCount=5)
                  ┌───────────┐                                    │       └─────┬─────┘
                  │    LLM    │                                    │             │ long polling
                  └───────────┘                                    ▼             ▼
                                   ┌──────────────────────────────────┐   ┌──────────────┐
                                   │ PostgreSQL: produtos, estoque,   │◀──│ order-worker │ reserva tudo-ou-nada
                                   │ pedidos, itens_pedido, outbox,   │   └──────────────┘
                                   │ conversas                        │
                                   └──────────────────────────────────┘
```

## Estrutura

```
docker-compose.yml           postgres, elasticmq, migrate, 3 serviços (+ perfil "test")
docker-compose.bedrock.yml   override: LLM_PROVIDER=bedrock e ~/.aws montado só no chat-api
elasticmq/elasticmq.conf     fila orders + DLQ orders-dlq (maxReceiveCount=5)
db/                          Alembic (migrations/), seed (seed/), bootstrap.py (job de migração), Dockerfile
services/chat-api/           FastAPI, loop de tool use, BedrockProvider/FakeProvider, página HTML
services/commerce-api/       catálogo, compatibilidade, kit, pedidos idempotentes, outbox
services/order-worker/       consumidor SQS, reserva de estoque, /health
```

Cada serviço tem `Dockerfile` multi-stage: `runtime` (imagem final, python:3.12-slim, usuário não-root
10001, sem deps de dev) e `test` (pytest). Os serviços são autocontidos — cada build context é a pasta
do serviço.

## Subindo localmente

### Modo fake (sem nenhuma credencial AWS)

```bash
cp .env.example .env          # opcional; os padrões já funcionam
docker compose up --build
```

Abra **http://localhost:8000**. O job `migrate` cria os usuários de banco, aplica as migrações e o seed
e termina; os serviços sobem depois dele. Painel do ElasticMQ: http://localhost:9325.

O `FakeProvider` é um "LLM" determinístico por regras: fala o mesmo protocolo de tool use do Bedrock,
então exercita de verdade o commerce-api, a fila e o worker. Todo dado que ele mostra veio de uma
ferramenta. Ele entende o roteiro de demonstração abaixo (frases parecidas também funcionam).

### Modo Bedrock (Claude Haiku 5.5)

```bash
aws sso login --profile meu-perfil            # se o perfil for SSO (IAM Identity Center)
export AWS_PROFILE=meu-perfil AWS_REGION=us-east-2 LLM_MODEL_ID=us.anthropic.claude-haiku-5-5
export LOCAL_UID=$(id -u) LOCAL_GID=$(id -g)  # Linux: o container precisa ler seus arquivos de ~/.aws
docker compose -f docker-compose.yml -f docker-compose.bedrock.yml up --build
```

No Windows (PowerShell, Docker Desktop) não é preciso `LOCAL_UID`/`LOCAL_GID`. A pasta montada é
`$HOME/.aws` ou, se `HOME` não estiver definido, `%USERPROFILE%\.aws`:

```powershell
aws sso login --profile meu-perfil
$env:AWS_PROFILE = "meu-perfil"; $env:AWS_REGION = "us-east-2"; $env:LLM_MODEL_ID = "us.anthropic.claude-haiku-5-5"
docker compose -f docker-compose.yml -f docker-compose.bedrock.yml up --build
```

| Região      | LLM_MODEL_ID (inference profile)   |
|-------------|------------------------------------|
| us-east-2   | `us.anthropic.claude-haiku-5-5`     |
| sa-east-1   | `global.anthropic.claude-haiku-5-5` |

Só o chat-api recebe `~/.aws`, e só nesse modo. O perfil precisa ter acesso ao modelo habilitado no
Bedrock e permissão `bedrock:InvokeModel` (a API Converse usa essa permissão).

### Testes

Os testes usam PostgreSQL real (criam um banco temporário, aplicam as migrações e removem no fim).

```bash
# Em containers (estágio "test" de cada imagem):
docker compose up -d postgres
docker compose --profile test run --rm tests-commerce
docker compose --profile test run --rm tests-worker
docker compose --profile test run --rm tests-chat

# Ou no host, com o postgres do compose em 127.0.0.1:5432:
cd services/commerce-api && pip install -r requirements.txt -r requirements-dev.txt
TEST_DB_ADMIN_URL=postgresql+psycopg://postgres:postgres_local@localhost:5432/postgres pytest
```

| Serviço | O que os testes cobrem |
|---|---|
| commerce-api (23) | idempotência do POST /orders (repetição, chave com outro conteúdo → 409, 8 requisições concorrentes → 1 pedido), preço recalculado no servidor, compatibilidade (conexão, adaptador/hub, SO, layout, Hz por porta, VESA/tamanho do suporte), desconto progressivo limitado por produto, outbox com fila fora do ar |
| order-worker (8) | confirmação, estoque insuficiente em um item → REJEITADO sem reservar nada, reentrega da mesma mensagem só apaga, entregas duplicadas simultâneas, dois pedidos pela última unidade, erro transitório não apaga, mensagem inválida fica para a DLQ |
| chat-api (9) | tool use com dados do commerce, correlation_id propagado, commerce fora do ar → resposta fixa e turno não gravado, LLM fora, limite de iterações, janela de histórico, idempotency key estável, BedrockProvider (inference profile, toolConfig, erro → LLMError) |

## Roteiro de demonstração

Na página (há botões com estas frases) ou via `curl -X POST localhost:8000/chat -H 'Content-Type: application/json' -d '{"conversation_id": "...", "message": "..."}'`:

1. **Busca** — "Tenho um MacBook Air só com USB-C e Bluetooth, uso macOS. Procuro teclado e mouse sem fio."
   → lista teclados e mouses que suportam macOS, com preço e estoque.
2. **Compatibilidade** — "Esses são compatíveis com meu Mac? Prefiro layout ABNT2."
   → ❌ Keychron K8 Pro e Magic Keyboard (só US), ❌ G305 (receptor USB-A, sugere adaptador), ✅ o resto.
3. **Montagem do kit** — "Monta um kit com o teclado, o mouse e um headset."
   → busca headsets, descarta os que precisam de USB-A/P2, e cota K380 + Pebble 2 + Zone Vibe 100:
   3 produtos = 8% → **R$ 889,64**.
4. **Negociação** — "Consegue um desconto maior?"
   → não inventa desconto; explica que com 4 produtos diferentes a faixa sobe para 10%.
5. "Então adiciona um mousepad." → recalcula: 4 produtos, 10% → **R$ 986,40**.
6. **Pedido** — "Fechado! Pode fazer o pedido no nome de Ana Souza."
   → `criar_pedido` com Idempotency-Key; pedido **PENDENTE**; o worker reserva em segundos.
7. **Status** — "Qual o status do pedido?" → **CONFIRMADO** (ou REJEITADO com o motivo).

Outras coisas para mostrar: um monitor de 240 Hz com "meu notebook entrega 144 Hz" gera alerta; o
"Samsung Odyssey G5 32" não serve no suporte Multilaser; o webcam Razer Kiyo Pro está sem estoque;
`docker compose stop commerce-api` faz o chat responder que não conseguiu consultar agora.

Para seguir um pedido nos três serviços: `docker compose logs | grep <correlation_id>` (o
`correlation_id` vem na resposta do POST /chat e no header `X-Correlation-ID`).

## Variáveis de ambiente

Todas as configurações vêm do ambiente; nenhuma credencial fica no código ou na imagem.
Em todos os serviços: `PORT` (padrão 8080), `LOG_LEVEL` (INFO), `DB_HOST`, `DB_PORT` (5432),
`DB_NAME` (setup_pronto), `DB_USER`, `DB_PASSWORD`, `DB_SSLMODE` (prefer; use `require` no RDS),
`DB_POOL_SIZE`, `DB_STATEMENT_TIMEOUT_MS`.

### chat-api

| Variável | Padrão | Descrição |
|---|---|---|
| `DB_USER` | chat_api | usuário com acesso só à tabela `conversas` |
| `COMMERCE_API_URL` | http://localhost:8081 | URL interna do commerce-api (Service Connect / ALB interno no ECS) |
| `COMMERCE_TIMEOUT_SECONDS` | 5 | timeout de cada chamada (1 nova tentativa em falha de rede) |
| `LLM_PROVIDER` | fake | `bedrock` ou `fake` |
| `LLM_MODEL_ID` | us.anthropic.claude-haiku-5-5 | inference profile do Bedrock |
| `AWS_REGION` | us-east-2 | região do Bedrock |
| `AWS_PROFILE` | — | só local; no ECS as credenciais vêm da IAM Role da task |
| `LLM_TIMEOUT_SECONDS` | 30 | read timeout da chamada ao Bedrock |
| `LLM_MAX_TOKENS` | 1024 | maxTokens da resposta |
| `LLM_TEMPERATURE` | (não enviado) | opcional |
| `MAX_TOOL_ITERATIONS` | 6 | máximo de chamadas ao LLM por turno |
| `CHAT_TURN_BUDGET_SECONDS` | 50 | orçamento de tempo por turno (abaixo do idle timeout de 60 s do ALB) |
| `HISTORY_MAX_MESSAGES` | 40 | mensagens do histórico enviadas ao LLM |
| `MAX_MESSAGE_CHARS` | 2000 | tamanho máximo da mensagem do cliente |

### commerce-api

| Variável | Padrão | Descrição |
|---|---|---|
| `DB_USER` | commerce_api | leitura de catálogo/estoque; escrita em pedidos, itens e outbox |
| `SQS_QUEUE_URL` | — | URL da fila `orders` (AWS) |
| `SQS_QUEUE_NAME` | — | alternativa local: resolve a URL pelo nome |
| `SQS_ENDPOINT_URL` | — | só local (`http://elasticmq:9324`) |
| `AWS_REGION` | us-east-2 | região do SQS |
| `OUTBOX_PUBLISHER_ENABLED` | true | liga o publicador do outbox |
| `OUTBOX_POLL_INTERVAL_SECONDS` | 1.0 | intervalo entre ciclos quando não há pendências |
| `OUTBOX_BATCH_SIZE` | 10 | eventos por `SendMessageBatch` (máx. 10) |
| `KIT_DISCOUNT_TIERS` | 2:5,3:8,4:10,5:12 | `produtos_diferentes:desconto%` |
| `MAX_QTD_POR_ITEM` | 10 | limite de unidades por item |

### order-worker

| Variável | Padrão | Descrição |
|---|---|---|
| `DB_USER` | order_worker | leitura de pedidos/itens; atualização de pedidos e estoque |
| `SQS_QUEUE_URL` / `SQS_QUEUE_NAME` / `SQS_ENDPOINT_URL` / `AWS_REGION` | — | como no commerce-api (uma das duas primeiras é obrigatória) |
| `SQS_WAIT_TIME_SECONDS` | 20 | long polling |
| `SQS_MAX_MESSAGES` | 10 | mensagens por recebimento |
| `HEALTH_MAX_LOOP_AGE_SECONDS` | 90 | /health falha se o loop do consumidor parar |

### migrate (db/)

`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_SSLMODE`, `DB_USER`/`DB_PASSWORD` (dono do schema, ex.: master do
RDS), `CHAT_DB_USER`/`CHAT_DB_PASSWORD`, `COMMERCE_DB_USER`/`COMMERCE_DB_PASSWORD`,
`WORKER_DB_USER`/`WORKER_DB_PASSWORD` (sem senha, o usuário não é criado/alterado), `SEED_ON_MIGRATE`
(true). O job é idempotente: pode rodar a cada deploy. O seed atualiza produtos pelo SKU, cria variantes
novas e **nunca sobrescreve o estoque** de variantes existentes.

## Esquema

| Tabela | Destaques |
|---|---|
| `produtos` | `sku` UNIQUE, `preco_centavos`, `desconto_max_pct`, `especificacoes` JSONB (conexoes, so_suportados, layouts, taxa_hz, taxa_hz_por_conexao, vesa, vesa_suportado, fornece…) com índice GIN |
| `estoque` | uma linha por variante (`switch`, `layout`, `cor`), `sku_variante` UNIQUE, `quantidade` CHECK ≥ 0 |
| `pedidos` | UUID, `idempotency_key` UNIQUE, `request_hash`, `status` PENDENTE/CONFIRMADO/REJEITADO, `motivo_rejeicao` obrigatório só quando REJEITADO, totais em centavos, `correlation_id` |
| `itens_pedido` | preço e desconto congelados no momento do pedido; UNIQUE (pedido, variante) |
| `outbox` | `payload` JSONB, `publicado_em`, `tentativas`, `ultimo_erro`; índice parcial nos não publicados |
| `conversas` | `mensagens` JSONB no formato Converse, `versao` para controle otimista |

## Decisões de projeto

- **Preço nunca vem do LLM.** `criar_pedido` envia só variantes e quantidades; o commerce-api recalcula o
  kit. Desconto progressivo conta **produtos diferentes** (5 mousepads iguais não são um kit) e cada linha
  recebe `min(faixa, desconto_max do produto)`. O LLM não vê o desconto máximo, só a próxima faixa.
- **Idempotência em duas camadas.** O chat-api deriva a Idempotency-Key de conversa + nome + itens, então
  o LLM chamar `criar_pedido` duas vezes, ou uma retentativa de rede, não duplica o pedido. No commerce-api,
  `INSERT … ON CONFLICT DO NOTHING` sobre a UNIQUE resolve corridas; mesma chave com outro conteúdo → 409.
- **Outbox "pelo menos uma vez".** Pedido + evento na mesma transação; o publicador usa
  `FOR UPDATE SKIP LOCKED` (várias tasks podem rodar juntas). Se cair entre o envio e o commit, a mensagem
  sai de novo — o worker é idempotente.
- **Reserva tudo-ou-nada.** O worker trava a linha do pedido, ignora pedidos que não estão PENDENTE,
  baixa cada variante com `UPDATE … WHERE quantidade >= :q` em ordem de `estoque_id` (evita deadlock) e,
  se algo faltar, desfaz o savepoint e marca REJEITADO com o motivo.
- **Falhas.** Commerce fora do ar (rede, timeout, 5xx) encerra o turno com mensagem fixa, sem devolver a
  palavra ao LLM; erros 4xx voltam ao LLM como resultado de ferramenta para ele corrigir. Turnos que falham
  não entram no histórico. O `/health` do chat-api checa só o banco: se o commerce cair, o chat continua de pé
  e responde que não conseguiu consultar, em vez de ser reiniciado em cascata.
- **Mensagens inválidas** (JSON quebrado, pedido inexistente) não são apagadas: após 5 recebimentos vão
  para a DLQ para investigação. Erros transitórios também não apagam (voltam pela visibility timeout).

## Notas para o ECS

Detalhes por serviço (portas, variáveis e segredos, CPU/memória, IAM, job de migração): **[docs/infra-handoff.md](docs/infra-handoff.md)**.

- **Imagens**: `./push.sh <regiao> <conta>` (ou `.\push.ps1 <regiao> <conta>` no Windows) faz login no ECR,
  cria os repositórios `setup-pronto/<nome>` se faltarem e publica as 4 imagens com a tag do commit
  (`linux/amd64` por padrão; `PLATFORM=linux/arm64` para Graviton). Nada é gravado em
  disco (`PYTHONDONTWRITEBYTECODE`, bytecode pré-compilado), então dá para ligar `readonlyRootFilesystem`.
- **Migrações**: rode a imagem `db` como tarefa avulsa (`aws ecs run-task`) antes de atualizar os serviços,
  com as credenciais do master vindas do Secrets Manager.
- **Segredos**: injete `DB_PASSWORD` via `secrets` da task definition (Secrets Manager/SSM). Um segredo
  por serviço, cada um com o seu usuário de banco.
- **Health checks**: todos expõem `GET /health` na porta 8080 (200 ok / 503). O `HEALTHCHECK` do Dockerfile
  vale para o compose; no ECS, repita o comando no `healthCheck` da task definition:
  `python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"`.
  Só o chat-api fica atrás do ALB público.
- **IAM (task roles)**:
  - chat-api: `bedrock:InvokeModel` no ARN do inference profile e nos ARNs do foundation model nas regiões
    para onde o perfil roteia (para o perfil `global.*`, confira na documentação de cross-region inference
    quais ARNs a política precisa cobrir).
  - commerce-api: `sqs:SendMessage` (e `sqs:GetQueueUrl` se usar `SQS_QUEUE_NAME`) na fila `orders`.
  - order-worker: `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:ChangeMessageVisibility`,
    `sqs:GetQueueAttributes` na fila `orders`.
- **Fila**: visibility timeout de 60 s, receive wait de 20 s, redrive para `orders-dlq` com `maxReceiveCount=5`.
- **Métricas a partir dos logs** (JSON no stdout → CloudWatch Logs via awslogs). Exemplos de metric filter:
  - latência do LLM: `{ $.event = "llm_call" && $.success IS TRUE }`, valor `$.latency_ms`
  - falhas do LLM: `{ $.event = "llm_call" && $.success IS FALSE }`
  - commerce indisponível: `{ $.event = "commerce_unavailable" }`
  - pedidos por resultado: `{ $.event = "order_processed" && $.resultado = "REJEITADO" }`
  - mensagens problemáticas: `{ $.event = "poison_message" || $.event = "order_not_found" }`
