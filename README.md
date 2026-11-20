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

**Implantar na AWS:** `cd infra/terraform && ./deploy.sh sa-east-1`: um comando, sem passos manuais, cria a
infraestrutura completa e a aplicação, e imprime a URL ([detalhes](#implantar-na-aws-terraform)).
**Rodar no computador:** `docker compose up --build`, depois abrir http://localhost:8000, sem nenhuma credencial
([detalhes](#subindo-localmente)).

## Arquitetura na AWS

![Arquitetura na AWS](docs/arquitetura.svg)

Fonte editável em [`docs/arquitetura.svg`](docs/arquitetura.svg) (PNG para slides: `docs/arquitetura.png`).

| Camada | Recursos (todos criados pelo Terraform) |
|---|---|
| Rede | VPC 10.30.0.0/16 em 2 zonas: sub-redes públicas (ALB, NAT), de aplicação e de dados, privadas; IGW, 1 NAT, endpoint S3 |
| Entrada | Application Load Balancer em HTTP (porta 80), único ponto público; health check em `/health` |
| Computação | ECS sobre EC2: Auto Scaling Group (t3.medium, 2–4 instâncias, sem IP público) + capacity provider com managed scaling; 3 serviços com auto scaling (chat por requisições no ALB, commerce por CPU, worker pelo tamanho da fila); Service Connect entre eles |
| Dados | RDS PostgreSQL privado e criptografado; um usuário de banco por serviço com GRANTs mínimos; migração como task avulsa a cada deploy |
| Mensageria | SQS `orders` + DLQ (5 tentativas); outbox no commerce-api e consumidor idempotente no order-worker |
| IA | Amazon Bedrock (Claude Haiku 5.5, API Converse com tool use) pela IAM Role da task do chat-api |
| Segurança | Secrets Manager (senhas fora do código, das imagens e do state), uma IAM Role por serviço, SGs encadeados ALB → instâncias → RDS, ECR imutável com scan |
| Observabilidade | logs JSON no CloudWatch, 15 metric filters, alarmes → SNS, dashboard; CloudTrail multirregião com alarmes de segurança |
| Entrega | CodeBuild constrói as imagens dentro do `terraform apply` (tag = hash do código); deploy rolling com circuit breaker |

## Estrutura

```
docker-compose.yml           postgres, elasticmq, migrate, 3 serviços (+ perfil "test")
docker-compose.bedrock.yml   override: LLM_PROVIDER=bedrock e ~/.aws montado só no chat-api
elasticmq/elasticmq.conf     fila orders + DLQ orders-dlq (maxReceiveCount=5)
db/                          Alembic (migrations/), seed (seed/), bootstrap.py (job de migração), Dockerfile
services/chat-api/           FastAPI, loop de tool use, BedrockProvider/FakeProvider, página HTML
services/commerce-api/       catálogo, compatibilidade, kit, pedidos idempotentes, outbox
services/order-worker/       consumidor SQS, reserva de estoque, /health
infra/terraform/             etapa automatizada: São Paulo (sa-east-1) com Terraform, state no S3 (ver o README de lá)
docs/etapa-manual-ohio/      etapa manual: scripts da AWS CLI usados em Ohio (us-east-2), na ordem
ops/                         operação e testes: status, carga em degraus, relatório do CloudWatch, demo, perda de
                             instância, drenagem, DLQ, teste da reconciliação, pausar/retomar
docs/roteiro-sao-paulo.md    passo a passo: deploy da versão nova, carga em degraus e relatório em sa-east-1
docs/roteiro-ensaio.md       ensaio e demonstração final: cada item exigido pelo enunciado, com o comando e o esperado
docs/roteiro-video.md        vídeo de entrega: as 6 etapas obrigatórias, o que mostrar e dizer, falhas e recuperação de desastres
docs/runbook.md              resposta a incidentes: o que fazer em cada alarme, consultas salvas, modelo de registro
docs/resultados/             resultados dos testes na AWS (carga, reconciliação)
```

Cada serviço tem `Dockerfile` multi-stage: `runtime` (imagem final, python:3.12-slim, usuário não-root
10001, sem deps de dev) e `test` (pytest). Os serviços são autocontidos — cada build context é a pasta
do serviço.

## Implantar na AWS (Terraform)

O Terraform em [`infra/terraform/`](infra/terraform/) cria **tudo** a partir de uma conta vazia, em um único
comando e sem nenhuma intervenção manual: rede, cluster, banco, fila, segredos, IAM, observabilidade, auditoria,
o build das imagens, a migração do banco e os serviços. Testado do zero: 173 recursos em ~9 min
([`docs/resultados/recriacao-sp.md`](docs/resultados/recriacao-sp.md)).

**Pré-requisitos**
- Conta AWS com permissão de administrador.
- Terraform ≥ 1.11, AWS CLI v2, `bash` e `python3`. O **AWS CloudShell** já tem tudo, menos o Terraform (veja abaixo).
  Docker não é necessário: as imagens são construídas na AWS pelo CodeBuild.
- Acesso ao Claude Haiku no Bedrock: em conta que nunca usou modelos da Anthropic, a AWS pode pedir uma vez o
  formulário de caso de uso no console do Bedrock. É um aceite da conta, fora do alcance do Terraform. Sem ele a
  infraestrutura sobe inteira, mas o chat responde "assistente indisponível".

**Implantar** (CloudShell de sa-east-1 ou qualquer terminal com credenciais da AWS):

```bash
# só no CloudShell: instalar o Terraform (uma vez por sessão)
curl -sLo /tmp/tf.zip https://releases.hashicorp.com/terraform/1.11.4/terraform_1.11.4_linux_amd64.zip
mkdir -p ~/bin && unzip -o /tmp/tf.zip -d ~/bin && export PATH=~/bin:$PATH TF_DATA_DIR=/tmp/tfdata

git clone <url deste repositório> && cd projetocloud/infra/terraform
./deploy.sh sa-east-1            # ~10 min; no fim imprime: Endereço da aplicação: http://setup-pronto-alb-….elb.amazonaws.com/
```

O `deploy.sh` cria o bucket do state remoto (S3, criptografado e versionado) se ele não existir, faz o
`terraform init` apontando para ele e roda `terraform apply -auto-approve`. Outras ações:

```bash
./deploy.sh sa-east-1 plan       # mostra o que mudaria
./deploy.sh sa-east-1 destroy    # remove todo o ambiente
```

Nova versão do código: `./deploy.sh` de novo. A tag das imagens é um hash do código, então só o que mudou é
reconstruído e implantado (rolling deploy com circuit breaker). Detalhes, arquivos e decisões:
[`infra/terraform/README.md`](infra/terraform/README.md).

**Custo**: o ambiente cobra por hora enquanto existe (EC2, NAT Gateway, ALB e RDS são os maiores itens).
`./deploy.sh <regiao> destroy` zera o custo; `ops/pausar.sh <regiao>` só para as instâncias e o RDS, sem destruir nada.

### Ambientes do grupo

| Etapa | Região | Como foi feita | Endereço |
|---|---|---|---|
| Manual | Ohio (us-east-2) | `docs/etapa-manual-ohio/` (AWS CLI no CloudShell) | pausado fora das sessões (`ops/retomar.sh us-east-2`) |
| Terraform | São Paulo (sa-east-1) | `infra/terraform/` (state no S3) | `terraform output url`, o DNS do ALB em HTTP |

Não há senha nem chave para repassar: os segredos ficam no Secrets Manager, e cada pessoa entra pelo IAM Identity
Center (CloudShell ou `aws sso login`). `./push.sh` continua disponível para publicar imagens à mão (com
`-var image_tag`).

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
export LOCAL_UID=$(id -u) LOCAL_GID=$(id -g)  # só no Linux
docker compose -f docker-compose.yml -f docker-compose.bedrock.yml up --build
```

O chat-api roda por padrão como `10001:10001`, o usuário `app` da imagem. **No Linux** o bind mount
de `~/.aws` respeita as permissões do host, então é preciso exportar `LOCAL_UID`/`LOCAL_GID` com o
seu próprio UID/GID (`id -u` / `id -g`); sem isso o container não consegue ler as credenciais nem
renovar o token SSO. No macOS e no Windows (Docker Desktop) o padrão funciona sem variáveis extras.

No Windows (PowerShell) a pasta montada é `$HOME/.aws` ou, se `HOME` não estiver definido,
`%USERPROFILE%\.aws`:

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
| commerce-api (27) | idempotência do POST /orders (repetição, chave com outro conteúdo → 409, 8 requisições concorrentes → 1 pedido), preço recalculado no servidor, compatibilidade (conexão, adaptador/hub, SO, layout, Hz por porta, VESA/tamanho do suporte), desconto progressivo limitado por produto, outbox com fila fora do ar, reconciliação (pedido parado é reenviado uma vez por janela, recente/já processado não, travado para após N reenvios, uma task por vez) |
| order-worker (8) | confirmação, estoque insuficiente em um item → REJEITADO sem reservar nada, reentrega da mesma mensagem só apaga, entregas duplicadas simultâneas, dois pedidos pela última unidade, erro transitório não apaga, mensagem inválida fica para a DLQ |
| chat-api (16) | tool use com dados do commerce, correlation_id propagado, commerce fora do ar → resposta fixa e turno não gravado, LLM fora, limite de iterações, janela de histórico, histórico gravado limitado, idempotency key estável, BedrockProvider (inference profile, toolConfig, erro → LLMError), vitrine, acompanhamento só do pedido da própria conversa, kit e pedido reconstruídos do histórico, página servida |

### Status da validação

| Item | Status |
|---|---|
| Testes automatizados nos containers (commerce 27, worker 8, chat 16) | ✅ passando |
| `docker compose up --build` no modo fake: build das 4 imagens, job `migrate`, ElasticMQ e health checks | ✅ testado |
| Roteiro de demonstração completo no modo fake (kit de R$ 889,64 → R$ 986,40, pedido CONFIRMADO pelo worker) | ✅ testado |
| Modo Bedrock (`docker-compose.bedrock.yml`) com o Claude Haiku 5.5 real | ✅ testado |
| Deploy na AWS: manual em Ohio e Terraform em São Paulo (do zero, em um apply) | ✅ testado |
| Carga em degraus, perda/drenagem de instância, DLQ, reconciliação ([`docs/resultados/`](docs/resultados/)) | ✅ testado |

## Roteiro de demonstração

Na página, o consultor abre com um assistente de 3 passos (computador → o que procura → conexões, sistema,
layout) que monta a primeira mensagem; depois há sugestões dos próximos passos. Também dá para digitar as frases
abaixo ou usar `curl -X POST localhost:8000/chat -H 'Content-Type: application/json' -d '{"conversation_id": "...", "message": "..."}'`:

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
| `HISTORY_STORED_MAX_MESSAGES` | 200 | máximo de mensagens gravadas em `conversas.mensagens`; acima disso ficam só as mais recentes (evento `conversation_trimmed`) |
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
| `RECONCILIACAO_ENABLED` | true | liga a reconciliação de pedidos PENDENTE parados (só roda com o publicador ligado) |
| `RECONCILIACAO_INTERVALO_SECONDS` | 60 | intervalo entre ciclos |
| `RECONCILIACAO_IDADE_SECONDS` | 600 | idade mínima do pedido e da última publicação para reenviar |
| `RECONCILIACAO_MAX_REENVIOS` | 3 | depois disso o pedido conta como travado (métrica `OrdersStuck`, alarme) |

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
- **Reconciliação de pedidos parados.** Se o evento de um pedido se perde depois de publicado (foi para a DLQ
  porque o banco ficou fora mais que 5 × visibility timeout, fila purgada…), o pedido ficaria PENDENTE para sempre.
  Um job no commerce-api (`app/reconciliacao.py`, a cada 60 s, uma task por vez via advisory lock) regrava o
  evento no outbox para pedidos PENDENTE cujo último evento foi publicado há mais de 10 min. O worker é idempotente,
  então reenviar à toa não tem efeito. Após 3 reenvios o pedido conta como travado (`OrdersStuck`, alarme
  `pedidos-travados`) e não é mais reenviado. Demonstração local: suba com `RECONCILIACAO_INTERVALO_SECONDS=5
  RECONCILIACAO_IDADE_SECONDS=20`, pare o order-worker, faça um pedido, purgue a fila `orders` e religue o worker:
  em ~20 s o pedido é CONFIRMADO, com o mesmo `correlation_id`.
- **DLQ**: `ops/dlq.sh <regiao> ver` mostra as mensagens sem tirá-las da DLQ; `ops/dlq.sh <regiao> reprocessar`
  devolve todas para a fila (redrive nativo do SQS) depois de corrigida a causa.

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
