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
| chat-api | `services/chat-api` | Único serviço público (ALB). `GET /` página da loja (vitrine + consultor), `POST /chat` (devolve também `ferramentas`, `kit` e `pedido_id` do turno), `GET /conversations/{id}`, `GET /api/vitrine` (catálogo do commerce), `GET /api/conversas/{cid}/pedidos/{pid}` (acompanhamento; só o pedido da própria conversa), `GET /health`. Loop de tool use com o LLM; histórico na tabela `conversas`. |
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
Hoje: commerce 27, worker 8, chat 16 testes.

Imagens: construídas pelo `terraform apply` (CodeBuild); `./push.sh <regiao> <conta>` só para uso manual com `-var image_tag`. Handoff de infra: `docs/infra-handoff.md`.
AWS do zero ou atualização: `infra/terraform/deploy.sh <regiao> [apply|plan|destroy]` (cria o bucket do state pelo
`bootstrap/` se faltar, init com o bucket derivado da conta, apply `-auto-approve`). É o caminho da entrega ("sem
intervenção manual"); o `envs/sa-east-1.backend.hcl` tem o bucket da conta do grupo e só serve para ela.

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
- **São Paulo (sa-east-1)**: `infra/terraform` (ver o README de lá), **um único `terraform apply`** (o CodeBuild
  constrói as imagens dentro do apply, `build.tf`), state no S3
  (`setup-pronto-tfstate-202757356229-sa-east-1`, criado por `bootstrap/`). Recriado do zero em 04/11 (115 recursos) e de
  novo em 13/11, já com borda e auditoria: destroy de 159 recursos em 6 min, fase 1 com 145 em 8 min, fase 2 com 14
  em 39 s; e de novo com o apply único: 173 recursos em 9 min a partir do state vazio (`docs/resultados/recriacao-sp.md`).
  Imagens com tag = hash do código (`819a55f1f9fb`, site novo da loja, implantadas em 18/11 a partir da working tree ainda não commitada). Scripts do apply repetem só a falha
  de propagação do IAM ("not authorized") — necessária em todo apply do zero.
  Cópia de trabalho antiga do Terraform em `~/tf` no CloudShell de sa-east-1: **dar pull antes de aplicar de lá**.
  O apply de 12/11 foi feito de um Windows com a AWS CLI (SSO) e o Terraform num container `amazon/aws-cli` + Terraform
  1.11.4 (o `run-migrate.sh` precisa de bash e python3). Para applies sem build/migração (só infra), basta
  `hashicorp/terraform:1.11.4` com `~/.aws` montado, `AWS_PROFILE=setup-pronto` e `TF_DATA_DIR` num volume Docker.
- **Entrada pelo ALB em HTTP** (desde 18/11): o endereço público é o DNS do ALB (`terraform output url`,
  `http://setup-pronto-alb-….sa-east-1.elb.amazonaws.com/`), SG com a 80 aberta e listener encaminhando ao chat.
  CloudFront + WAF (`edge.tf`, 12/11–18/11) foram removidos para ficar no escopo do enunciado; os resultados de 12/11 em
  `docs/resultados/` foram medidos com eles. `ops/comum.sh` exporta `URL` (o ALB); scripts e cargas devem usar `$URL`.
- Testes em São Paulo (04/11): perda abrupta da instância mais carregada → ASG repõe em ~1 min, ~1% de 502 por ~10 s;
  drenagem planejada sem erros. Resultados no doc "Setup Pronto — roteiro da apresentação".
- Testes em São Paulo (12/11), resultados em `docs/resultados/` (`relatorio-sp.{md,html,csv}`, `carga-sp.log`,
  `reconciliacao-sp.log`): carga em degraus 0,5→2→4→0,5 req/s por 45 min, 4.622 requisições, 3 respostas 502 do ALB
  no scale-out (99,935%); chat-api 2→6 tasks (1ª mudança 7 min após o degrau, 6 em 10 min), frota 3→4 instâncias
  (7 min); scale-in 6→2 tasks em 36 min (uma a cada ~6 min, cooldown 300 s) e 4→3 instâncias em 48 min, com a
  managed termination protection liberando só a instância vazia. Reconciliação: pedido com a mensagem purgada foi
  reenviado após ~10 min e CONFIRMADO com o mesmo `correlation_id`.
- Base da frota: 3 instâncias, não 2 — com target capacity 90%, 2 instâncias "necessárias" viram 3. Um deploy (200% das
  tasks) sobe a frota ao máximo (4) e ela leva ~15 min para voltar; espere estabilizar antes de medir.
- Pausar: `ops/pausar.sh` zera min/max/desejado do ASG e tira a proteção de scale-in (com 0 tasks e 0 instâncias a
  reserva do capacity provider é 100% > 90% e ele subiria uma instância). O RDS parado religa sozinho após 7 dias.
- No CloudShell, `AWS_REGION` vem definido e tem precedência sobre `AWS_DEFAULT_REGION`: scripts exportam os dois.
- Ohio fica pausado fora das sessões (`ops/pausar.sh`); **São Paulo fica ligado desde 18/11** (decisão do usuário, para o
  vídeo). `ops/retomar.sh` ou `terraform apply` religam.
- Operação e testes: `ops/` (status, carga, demo, perda de instância, drenagem, alarmes extras de Ohio).
- Service Connect: o cliente (chat-api) só enxerga os aliases que já existiam quando a task subiu — por isso o
  `aws_ecs_service.chat` depende do commerce-api. Se o chat responder `commerce_indisponivel` logo após criar os serviços,
  um `--force-new-deployment` no chat-api resolve.

## O que ainda falta
- Sem autenticação nem limite por IP no `POST /chat` (custo de tokens exposto a abuso; proposta na etapa 6). Histórico gravado limitado a `HISTORY_STORED_MAX_MESSAGES` (200).
- Prompt (regra 5, 13/11): aceite + nome depois do último `calcular_kit` já é a confirmação; validado com o Haiku real
  (`docs/resultados/confirmacao-pedido-bedrock.log`). Em produção em SP desde 13/11.
- Ohio ainda roda `37791f6`, sem a reconciliação: para atualizar, publicar `bc328c5` no ECR de Ohio, `TAG=... s4-tasks.sh`
  e rodar de novo `ops/ohio-alarmes-extra.sh` (metric filters e alarme `pedidos-travados`).
- Auditoria: `infra/terraform/audit.tf` (CloudTrail multirregião criado pelo Terraform de SP — não criar outro em Ohio,
  alarmes `seg-*`, consultas salvas) e `docs/runbook.md`. Alarme novo → acrescentar a seção correspondente no runbook.
  Aplicado em SP em 12/11.
- Diagrama: `docs/arquitetura.svg` (fonte, editável) e `docs/arquitetura.png` (gerado do SVG com o Edge headless:
  `msedge --headless=new --window-size=1600,1100 --screenshot=<png> file:///.../arquitetura.svg`). Mudou a infra → atualize os dois.
- Entrega final = **vídeo de 10–15 min em 6 etapas fixas** (instruções do professor) + Terraform no GitHub, que precisa
  criar tudo sem intervenção manual (feito: apply único). Roteiro do vídeo: `docs/roteiro-video.md` (inclui a tabela de
  falhas/DR e os cuidados para não expor segredos na gravação). **O repositório no GitHub é privado**: liberar ao professor.
- Demandas técnicas do plano até 20/11 concluídas em 13/11; falta a apresentação (slides e ensaio da demo). Ensaio:
  `docs/roteiro-ensaio.md` (gera em `docs/resultados/ensaio-*` as evidências em SP de substituição de task, perda e
  drenagem de instância e persistência; scripts novos `ops/teste-task.sh` e `ops/teste-persistencia.sh`). SP foi
  religado em 18/11 (`ops/retomar.sh`) e fica ligado; antes de gravar, confira que a frota voltou a 3 instâncias.
- Sem CI (pytest dos três serviços + build das imagens + `terraform validate` a cada push).
- Perda abrupta de instância gera ~1% de 502 por alguns segundos; para zerar, avaliar retentativa no cliente/ALB.
