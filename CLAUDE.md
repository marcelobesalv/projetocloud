# CLAUDE.md — contexto para continuar o projeto

## O que é
**Setup Pronto**: loja de periféricos (teclado, mouse, headset, monitor, webcam, acessórios) com um consultor
de IA que conversa em português, verifica compatibilidade com o equipamento do cliente, monta um kit com
desconto progressivo e registra o pedido. Projeto da disciplina de Computação em Nuvem: a aplicação roda
localmente com Docker Compose e deve ir para **ECS sobre EC2 + ALB + RDS PostgreSQL + SQS** sem mudar código.

Regra de ouro do domínio: **a IA nunca inventa produto, preço, estoque ou desconto** — tudo vem de ferramentas
(tool use) que chamam o commerce-api.

## Arquitetura
| Componente | Pasta | Papel |
|---|---|---|
| chat-api | `services/chat-api` | Único serviço público (ALB). `GET /` página de chat, `POST /chat`, `GET /conversations/{id}`, `GET /health`. Loop de tool use com o LLM; histórico na tabela `conversas`. |
| commerce-api | `services/commerce-api` | Interno. Catálogo (`/products`), estoque (`/stock/{sku}`), compatibilidade (`/compatibility`), cotação de kit (`/kits/quote`), pedidos (`POST /orders` com `Idempotency-Key`, `GET /orders/{id}`). Thread de background publica o outbox no SQS. |
| order-worker | `services/order-worker` | Consome a fila `orders` (long polling), reserva estoque tudo-ou-nada, marca CONFIRMADO/REJEITADO. Só expõe `/health`. |
| migrate (job) | `db/` | Cria um usuário de banco por serviço, aplica Alembic, aplica GRANTs mínimos e roda o seed. Idempotente; roda antes de cada deploy. |

Fluxo de pedido: chat-api → `POST /orders` → commerce grava `pedidos` + `itens_pedido` + `outbox` numa transação
→ publicador envia ao SQS → worker reserva estoque → status visível via `status_pedido`.

Ferramentas do LLM (`services/chat-api/app/tools.py`): `buscar_produtos`, `consultar_estoque`,
`verificar_compatibilidade`, `calcular_kit`, `criar_pedido`, `status_pedido`.

## Decisões e porquês
- **LLM**: Claude Haiku 5.5 no Bedrock via **inference profile** (a chamada direta pelo ID do modelo não é suportada):
  `us.anthropic.claude-haiku-5-5` em us-east-2, `global.anthropic.claude-haiku-5-5` em sa-east-1. Vem de
  `LLM_MODEL_ID`; região de `AWS_REGION`. API **Converse** com `toolConfig`. Credenciais só pela cadeia padrão do
  boto3 (AWS_PROFILE local, IAM Role da task no ECS). `temperature` não é enviado por padrão.
- **Interface de provedor** (`app/llm/base.py`): `BedrockProvider` e `FakeProvider` (determinístico, por regras,
  entende o roteiro do README). O formato das mensagens é o do Converse — é também o que fica gravado em `conversas`.
- **Falhas no chat**: commerce fora (rede/timeout/5xx) → resposta fixa e o turno acaba **sem devolver a palavra ao
  LLM** (ele não tem chance de improvisar). 4xx vira `toolResult` com status error para o LLM corrigir. LLM fora →
  resposta fixa. Turnos que falham não são gravados. Limites: `MAX_TOOL_ITERATIONS` e `CHAT_TURN_BUDGET_SECONDS`
  (50 s, abaixo do idle timeout de 60 s do ALB). `/health` do chat checa só o banco, para não cair em cascata.
- **Idempotência do pedido em duas camadas**: o chat-api deriva a `Idempotency-Key` de conversa + nome + itens
  (LLM chamando `criar_pedido` duas vezes não duplica). No commerce: UNIQUE em `pedidos.idempotency_key` +
  `INSERT … ON CONFLICT DO NOTHING`; mesma chave com corpo diferente (`request_hash`) → 409; repetição → 200 com
  o pedido original e header `Idempotent-Replay: true`.
- **Outbox**: pedido + evento na mesma transação; publicador usa `FOR UPDATE SKIP LOCKED` (várias tasks podem rodar)
  e `SendMessageBatch`. Entrega **pelo menos uma vez** — duplicatas são esperadas e o worker as absorve.
- **Reserva tudo-ou-nada** (`order-worker/app/reserva.py`): trava a linha do pedido (`FOR UPDATE`), só processa
  `PENDENTE`, baixa cada variante com `UPDATE … WHERE quantidade >= :q` em ordem de `estoque_id` (evita deadlock)
  dentro de um savepoint; se algo faltar, desfaz e marca REJEITADO com motivo legível.
- **Reconciliação** (`commerce-api/app/reconciliacao.py`): thread que, a cada `RECONCILIACAO_INTERVALO_SECONDS`, regrava no
  outbox o evento de pedidos PENDENTE cujo último evento foi publicado há mais de `RECONCILIACAO_IDADE_SECONDS` (evento
  perdido: DLQ, fila purgada). Uma task por vez (`pg_try_advisory_xact_lock`, pois o commerce não tem UPDATE em pedidos).
  Após `RECONCILIACAO_MAX_REENVIOS` o pedido conta como travado: log `reconciliation_cycle.travados` → métrica
  `OrdersStuck` → alarme `pedidos-travados`. DLQ: `ops/dlq.sh <regiao> ver|reprocessar` (StartMessageMoveTask).
- **Worker e a fila**: sucesso ou pedido já processado → apaga a mensagem. Erro transitório de banco → não apaga
  (volta pela visibility timeout). Mensagem inválida ou pedido inexistente → não apaga, vai para a DLQ após
  `maxReceiveCount=5`.
- **Preço**: dinheiro sempre em **centavos inteiros**, arredondamento HALF_UP por linha. O LLM nunca manda preço;
  o commerce recalcula. Desconto progressivo conta **produtos diferentes** (`KIT_DISCOUNT_TIERS=2:5,3:8,4:10,5:12`),
  cada linha limitada por `produtos.desconto_max_pct`. O LLM só vê a faixa atual e a próxima, não o máximo.
- **Compatibilidade** (`commerce-api/app/compat.py`, função pura): conexões (hub/adaptador na lista conta), SO,
  layout ABNT2/US, Hz (inclusive limite por porta e de hub = 60 Hz) e pares suporte×monitor (VESA, tamanho, peso).
  Status `compativel | alerta | incompativel`.
- **Um usuário de banco por serviço** com GRANTs mínimos (`db/bootstrap.py`, dicionário `GRANTS`).
- **Fila no ambiente local**: `SQS_QUEUE_NAME` + `SQS_ENDPOINT_URL` (resolve URL pelo nome no ElasticMQ). Na AWS:
  `SQS_QUEUE_URL`.
- **Correlation id**: gerado no chat-api (middleware), enviado ao commerce em `X-Correlation-ID`, gravado em
  `pedidos.correlation_id`, no corpo e em atributo da mensagem SQS, e posto no contexto de log do worker.

## Como rodar
```bash
docker compose up --build                     # modo fake, sem AWS; http://localhost:8000
AWS_PROFILE=... LOCAL_UID=$(id -u) LOCAL_GID=$(id -g) \
  docker compose -f docker-compose.yml -f docker-compose.bedrock.yml up --build   # Bedrock
docker compose up -d postgres && docker compose --profile test run --rm tests-commerce   # idem tests-worker, tests-chat
```
No host: `cd services/<svc> && pip install -r requirements.txt -r requirements-dev.txt &&
TEST_DB_ADMIN_URL=postgresql+psycopg://postgres:postgres_local@localhost:5432/postgres pytest`.
Os testes criam um banco temporário, aplicam as migrações de `db/` (`DB_MIGRATIONS_DIR`) e o apagam no fim.
Hoje: commerce 27, worker 8, chat 9 testes.

Publicar imagens: `./push.sh <regiao> <conta>` (ou `push.ps1`). Handoff de infra: `docs/infra-handoff.md`.

## Convenções
- Python 3.12, FastAPI, SQLAlchemy Core com SQL explícito (`text()`), psycopg 3. Sem ORM.
- Nomes de domínio em português (`pedidos`, `estoque`, `cotar`, `processar_pedido`); termos técnicos em inglês.
- Cada serviço é autocontido (build context = pasta do serviço). `logs.py` e `db.py` são **cópias** de propósito
  nos três serviços — mudou em um, mude nos outros.
- Config: dataclass `Settings.from_env()` em `app/config.py`; nada de segredo em código ou imagem.
- `create_app(settings, engine=..., ...)` com injeção de dependências para os testes; entrypoint `python -m app`.
- Logs: JSON no stdout, sempre com `correlation_id` e um campo `event` (`llm_call`, `tool_call`, `order_created`,
  `outbox_published`, `order_processed`, `commerce_unavailable`, `poison_message`…) — viram metric filters.
- Erros HTTP: `{"erro": "mensagem em português", "codigo": "snake_case"}`.
- Migrações: nova revisão Alembic em `db/migrations/versions/` com SQL explícito; tabela nova → adicionar GRANTs em
  `db/bootstrap.py`. Seed nunca sobrescreve estoque existente.
- Testes: banco real, nada de mock de banco; SQS e commerce simulados com stubs simples.

## Estado da implantação (nov/2026)
- **Ohio (us-east-2)**: montado à mão via CloudShell (scripts `s1`–`s6` no home do CloudShell de Ohio), imagem `37791f6`,
  validado com roteiro completo, teste de carga (2→6 tasks, 3→4 instâncias, 0 erros) e testes de falha (DLQ, commerce fora).
- **São Paulo (sa-east-1)**: `infra/terraform` (ver o README de lá), em duas fases (`deploy_app`), state no S3
  (`setup-pronto-tfstate-202757356229-sa-east-1`, criado por `bootstrap/`). Recriado do zero em 04/11 (destroy de 115
  recursos + apply) e rodando a imagem `82b38f5`, publicada do código com `push.ps1`. Cópia de trabalho do Terraform em
  `~/tf` e scripts em `~/ops` no CloudShell de sa-east-1 (`TF_DATA_DIR=/tmp/tfdata`, Terraform 1.11.4 em `~/bin`).
- Testes em São Paulo (04/11): perda abrupta da instância mais carregada → ASG repõe em ~1 min, ~1% de 502 por ~10 s;
  drenagem planejada sem erros. Resultados no doc "Setup Pronto — roteiro da apresentação".
- No CloudShell, `AWS_REGION` vem definido e tem precedência sobre `AWS_DEFAULT_REGION`: scripts exportam os dois.
- Os dois ambientes ficam pausados fora das sessões (`ops/pausar.sh`); `ops/retomar.sh` ou `terraform apply` religam.
- Operação e testes: `ops/` (status, carga, demo, perda de instância, drenagem, alarmes extras de Ohio).
- Service Connect: o cliente (chat-api) só enxerga os aliases que já existiam quando a task subiu — por isso o
  `aws_ecs_service.chat` depende do commerce-api. Se o chat responder `commerce_indisponivel` logo após criar os serviços,
  um `--force-new-deployment` no chat-api resolve.

## O que ainda falta
- O Haiku às vezes pede confirmação antes de `criar_pedido`; decidir se o prompt deve aceitar "Fechado! Pode fazer o
  pedido" como confirmação suficiente.
- Sem autenticação nem rate limiting no `POST /chat`; o histórico em `conversas.mensagens` cresce sem limite.
- Reconciliação e `ops/dlq.sh` ainda não foram implantados na AWS: precisam de imagem nova (`push`), da migração
  0002 e, em Ohio, de rodar de novo `ops/ohio-alarmes-extra.sh` (metric filters e alarme `pedidos-travados`).
  Passo a passo para São Paulo, junto com a carga em degraus e o relatório: `docs/roteiro-sao-paulo.md`
  (`ops/carga.py --degraus ... --csv`, `ops/metricas.py --carga`, `ops/teste-reconciliacao.sh`). Resultados vão em
  `docs/resultados/`.
- Sem CI (pytest dos três serviços + build das imagens + `terraform validate` a cada push).
- Ohio ainda roda `37791f6`: atualizar as task definitions para `82b38f5` (já está no ECR de Ohio).
- Perda abrupta de instância gera ~1% de 502 por alguns segundos; para zerar, avaliar retentativa no cliente/ALB.
