# Roteiro do vídeo — Setup Pronto

Segue a estrutura obrigatória das instruções de entrega: **seis etapas, nesta ordem, de 10 a 15 min**, abordagem
top-down (negócio → experiência → arquitetura → AWS → decisões → riscos). Os tempos somam 13 min 30 s.

Isto é um guia de **o que mostrar e o que precisa ser dito**, não um texto para ler: as instruções pedem compreensão
própria e o professor pode arguir qualquer integrante sobre qualquer parte. Todos os integrantes narram; sugestão de
divisão na última coluna (troquem pelos nomes).

| # | Etapa | Tempo | Tela principal | Quem |
|---|---|---|---|---|
| 1 | Startup e proposta de valor | 1:30 | slide | Integrante 1 |
| 2 | Demonstração real | 2:00 | página do chat | Integrante 1 |
| 3 | Diagrama de arquitetura | 2:00 | `docs/arquitetura.png` | Integrante 2 |
| 4 | Navegação pelo console AWS | 2:30 | console (sa-east-1) | Integrante 3 |
| 5 | Conceitos e decisões de design | 3:30 | trechos de Terraform e de código | Integrantes 2 e 4 |
| 6 | Falhas, riscos e recuperação de desastres | 2:00 | slide + dashboard | Integrante 4 |

## Antes de gravar

- [ ] Ambiente ligado e estável: `terraform apply -var-file=envs/sa-east-1.tfvars` (ou `ops/retomar.sh sa-east-1`),
      esperar a frota voltar a 3 instâncias (~15 min). URL atual: `terraform output url`.
- [ ] Uma conversa de demonstração já feita **antes** (para a etapa 4 ter logs, métricas e um pedido no dashboard).
- [ ] Navegador com zoom de 125–150% (legibilidade), abas abertas na ordem da etapa 4, região **sa-east-1**.
- [ ] **Segredos fora da tela** (as instruções proíbem expor credenciais):
  - não clicar em "Retrieve secret value" no Secrets Manager;
  - não mostrar o portal SSO (e-mail), terminal com tokens, nem `~/.aws`.
- [ ] Depois de gravar: `bash ops/pausar.sh sa-east-1`.

---

## Etapa 1 — Startup e proposta de valor (1:30)

Tela: um slide com nome, problema, público, proposta de valor e modelo de receita.

Perguntas que precisam ser respondidas:

- **Nome, produto e problema.** *Setup Pronto*: loja de periféricos (teclado, mouse, headset, monitor, webcam,
  acessórios) com um consultor de IA que monta o kit certo para o equipamento do cliente. Problema: comprar periférico
  online é cheio de incompatibilidade escondida — USB-C × USB-A, receptor que precisa de adaptador, macOS × Windows,
  layout ABNT2 × US, monitor de 240 Hz num notebook que entrega 144 Hz, suporte que não aguenta o monitor (VESA, peso).
  Isso vira devolução, carrinho abandonado e cliente insatisfeito.
- **Público-alvo.** Quem está montando ou atualizando um setup sem ser especialista: trabalho remoto e híbrido,
  estudantes, criadores de conteúdo, gamers iniciantes; e empresas equipando times em home office.
- **Proposta de valor e diferenciais.**
  - conversa em português, a qualquer hora, que pergunta o equipamento e **verifica compatibilidade de verdade**
    (regras de portas, SO, layout, Hz, VESA) antes de recomendar;
  - **nunca inventa** produto, preço, estoque ou desconto: tudo vem do catálogo e do estoque reais — o que um chatbot
    genérico não garante;
  - monta o **kit completo** com desconto progressivo por produtos diferentes, e fecha o pedido na própria conversa.
- **Receita e escala.**
  - como varejo: margem dos produtos; o kit com desconto progressivo aumenta o **ticket médio** (no roteiro de
    demonstração, de 2 produtos pedidos para um kit de 4, R$ 986,40);
  - como produto para outras lojas (SaaS): assinatura por loja + valor por conversa ou por pedido fechado;
  - **viabilidade**: o custo marginal de uma conversa é o do LLM (tokens) — medido no dashboard pelas métricas
    `LLMInputTokens`/`LLMOutputTokens` — e a infraestrutura escala por tasks e instâncias, pagando pelo uso.
    *(Antes de gravar, calculem o custo por conversa com os tokens da demonstração e o preço do Haiku no Bedrock e
    coloquem o número no slide; não usem um número sem medir.)*

Objetivo declarado nas instruções: mostrar uma oportunidade comercial concreta.

## Etapa 2 — Demonstração real da aplicação (2:00)

Tela: a página do chat na URL do ALB (`terraform output url`, `http://setup-pronto-alb-….sa-east-1.elb.amazonaws.com/`).
Fazer ao vivo; cortes são permitidos, simulação não.

O que a página mostra e vale apontar (tudo vem do sistema, não do texto do LLM):
- **vitrine** com o catálogo, preço e estoque reais (`GET /api/vitrine` → commerce-api);
- embaixo de cada resposta do consultor, as **ferramentas que ele chamou** ("catálogo", "compatibilidade", "cotação do
  kit", "pedido registrado"): é a prova visual de que a IA consultou e alterou o estado do sistema;
- o cartão **"Seu kit"** com a cotação do `calcular_kit` (itens, desconto por linha, total e a próxima faixa);
- o **acompanhamento do pedido** (recebido → reserva de estoque → confirmado), atualizado sozinho a partir do banco.

1. "Tenho um MacBook Air só com USB-C e Bluetooth, uso macOS. Procuro teclado e mouse sem fio." → lista com preço e
   estoque **do catálogo**.
2. "Esses são compatíveis com meu Mac? Prefiro layout ABNT2." → descarta os que são só US e o mouse com receptor USB-A
   (sugere adaptador): a regra de compatibilidade rodou no commerce-api.
3. "Monta um kit com o Logitech K380 ABNT2 grafite, o Pebble Mouse 2 grafite e o headset Zone Vibe 100 grafite." →
   3 produtos, 8% → **R$ 889,64**.
4. "Consegue um desconto maior?" → **não inventa**; oferece a próxima faixa (4 produtos, 10%).
5. "Então adiciona o mousepad Speed XL preto." → **R$ 986,40**.
6. "Fechado! Pode fazer o pedido no nome de Ana Souza." → número do pedido, **PENDENTE**; o cartão de
   acompanhamento aparece e, em poucos segundos, passa sozinho para **CONFIRMADO** (o worker reservou o estoque pela
   fila). Bom momento para dizer "isto é a comunicação assíncrona acontecendo".
7. "Qual o status do pedido?" → o consultor confirma pela ferramenta `status_pedido`.
8. **Persistência**: recarregar a página (F5) — a conversa, o kit e o acompanhamento voltam inteiros, lidos do banco
   (`GET /conversations/{id}`), e o status continua CONFIRMADO. O estoque do produto na vitrine também caiu. (Opcional, se couber: mostrar o pedido no log da consulta salva da etapa 4.)

Valor para o cliente (uma frase): em dois minutos ele saiu de "o que serve no meu Mac?" para um kit compatível,
com o melhor desconto válido, comprado — sem abrir dez abas de especificação.

## Etapa 3 — Diagrama de arquitetura (2:00)

Tela: `docs/arquitetura.png` (fiel ao Terraform). Percorrer o fluxo numerado — as instruções pedem o caminho da
requisição, a comunicação assíncrona e o armazenamento, com protocolos e dependências:

1. **Cliente → ALB** (HTTP, porta 80, pela Internet Gateway). O ALB fica nas sub-redes públicas das **2 zonas** e é o
   único ponto de entrada: instâncias e banco não têm IP público.
2. **ALB → task do chat-api** (porta dinâmica na instância EC2 privada, health check em `/health`).
3. **chat-api ↔ Bedrock** (API Converse, HTTPS pela NAT): o LLM pede ferramentas; não tem acesso direto a dados.
4. **chat-api → commerce-api** (HTTP interno pelo **Service Connect**): catálogo, compatibilidade, cotação, pedido.
5. **commerce-api → RDS** (PostgreSQL, 5432, sub-rede de dados): pedido + itens + evento de **outbox** na mesma transação.
6. **Publicador do outbox → SQS** `orders` (assíncrono, pelo menos uma vez); **order-worker** consome com long polling.
7. **worker → RDS**: reserva o estoque tudo-ou-nada; CONFIRMADO ou REJEITADO. Falhou 5 vezes → **DLQ**.

Pontos do diagrama a nomear: VPC 10.30.0.0/16 em 2 zonas; sub-redes pública/aplicação/dados; ECS sobre EC2 com ASG
e Capacity Provider; ECR; Secrets Manager; CloudWatch/SNS; IAM; CloudTrail. Dependências: chat depende de commerce e
do Bedrock (se caírem, resposta fixa); commerce depende do RDS e, de forma desacoplada, da fila; worker depende da fila
e do RDS.

## Etapa 4 — Navegação pelo console AWS (2:30)

Para **cada** tela: o que é, para que serve aqui e com o que se conecta (não basta abrir a página inicial).
Ordem sugerida (~15 s por tela):

| Tela | O que mostrar e dizer |
|---|---|
| VPC → Sub-redes | 6 sub-redes (pública, aplicação, dados × 2 zonas) e os CIDRs |
| VPC → Tabelas de rotas | pública → Internet Gateway; aplicação → NAT; dados → **sem saída** (só a VPC) |
| EC2 → Security Groups | ALB: 80 aberta à internet (é a porta de entrada); instâncias: só do SG do ALB (portas dinâmicas) e entre si; RDS: só das instâncias na 5432 |
| EC2 → Load Balancers / Target Groups | ALB em 2 zonas; target group com alvos `instância:porta dinâmica`, health check `/health`, todos *healthy*; listener HTTP:80 encaminhando ao target group |
| ECS → Cluster → Serviços | 3 serviços, desired/running, estratégia do capacity provider, circuit breaker; aba de auto scaling (política de cada um) |
| ECS → Tasks / Task definition | reserva e limite de CPU/memória, `secrets` com o **ARN** do segredo (não o valor), log driver do CloudWatch |
| EC2 → Instâncias / Auto Scaling Groups | instâncias sem IP público nas sub-redes de aplicação; ASG 2–4 com proteção de scale-in |
| ECS → Capacity providers | managed scaling com alvo de 90% e termination protection |
| ECR | 4 repositórios, tag = hash do código, scan na publicação |
| RDS | não acessível publicamente, sub-redes de dados, criptografado, backup automático |
| SQS | fila `orders` com redrive para `orders-dlq` (máximo de 5 recebimentos), mensagens em voo |
| IAM → Roles | execution role (só lê os 4 segredos), uma role por serviço: chat só o Bedrock, commerce só envia à fila, worker só consome |
| Secrets Manager | lista dos segredos (sem revelar valores); senha master gerenciada pelo próprio RDS |
| CloudWatch | dashboard `setup-pronto`; Logs Insights → consulta salva `pedido-por-correlation-id` com o pedido da demo passando pelos 3 serviços; alarmes |
| (se sobrar tempo) CloudTrail | trilha multirregião gravando; alarmes `seg-*` |

## Etapa 5 — Conceitos, decisões de design e integração (3:30)

Relacionar cada decisão a um requisito e mostrar onde está no código. Sugestão de trechos (abrir no editor, fonte
grande):

**Rede e comunicação (~50 s)** — `infra/terraform/network.tf` (3 camadas × 2 zonas, rotas), `security.tf` (SGs
encadeados: internet → ALB na 80 → instâncias nas portas dinâmicas → RDS na 5432) e `alb.tf`. Tráfego de entrada só
pelo ALB; interno por Service Connect; RDS sem rota para fora. Uma NAT só (custo) e endpoint S3 para as
camadas das imagens.

**Containers e elasticidade (~60 s)** — `build.tf` (imagens construídas pelo CodeBuild, tag = hash do código),
`ecs.tf` (launch template com a AMI do SSM, ASG, capacity provider com managed scaling 90%), `app.tf` (placement:
espalha entre zonas e depois binpack por memória; políticas de target tracking: chat por requisições por task,
commerce por CPU, worker pela profundidade da fila; health checks e circuit breaker). Escala de tasks × instâncias:
a task pede CPU/memória; quando não cabe, o capacity provider sobe instância (medido: 2→6 tasks, 3→4 instâncias,
`docs/resultados/relatorio-sp.html`).

**Dados, mensageria e segurança (~60 s)** — chamadas síncronas (chat → commerce) × assíncronas (outbox → SQS →
worker). Retentativas e duplicidade: `Idempotency-Key` do pedido (`services/commerce-api/app/orders.py`, `ON CONFLICT
DO NOTHING`), worker idempotente e reserva tudo-ou-nada (`order-worker/app/reserva.py`), DLQ após 5 tentativas,
reconciliação que reenvia pedidos parados. Segurança: um usuário de banco por serviço com GRANTs mínimos
(`db/bootstrap.py`), IAM por serviço (`iam.tf`), senhas fora do código, das imagens e do state (`secret_string_wo`),
CloudTrail com alarmes de segurança.

**Terraform e decisões arquiteturais (~40 s)** — um único `terraform apply` cria o ambiente inteiro em ~9 min,
sem passo manual (`docs/resultados/recriacao-sp.md`); state remoto no S3 com trava; parâmetros por região em
`envs/sa-east-1.tfvars`; dependências regionais resolvidas na região (AMI pelo SSM, zonas por data source, imagens no
ECR local, endpoints da própria região). Compromissos: custo (Single-AZ, 1 NAT, t3.medium) × disponibilidade (ver
etapa 6) × segurança (HTTP sem TLS e `POST /chat` sem autenticação nem limite por IP: escopo do MVP).

## Etapa 6 — Falhas, riscos e recuperação de desastres (2:00)

Pergunta obrigatória: **"Quando a plataforma falha, quais são os riscos e como recuperar o funcionamento em caso de
desastre?"** Base: tabela abaixo (um slide) + o dashboard e o runbook (`docs/runbook.md`).

| Falha | Impacto (clientes / dados / receita) | Como detectar | Recuperação automática (existe) | Manual ou proposta |
|---|---|---|---|---|
| Task trava ou cai | nenhum percebido (outras tasks atendem) | `*-tasks-abaixo-do-minimo`, hosts não saudáveis no ALB | ECS repõe a task; ALB tira do balanceamento | — |
| Perda de instância EC2 | ~1% de 502 por ~10 s | `asg-abaixo-do-minimo`, `UnHealthyHostCount` | ASG repõe em ~1 min; capacity provider recoloca as tasks | — |
| Zona de disponibilidade fora | **se for a zona do RDS ou da NAT: plataforma fora** (o `/health` do chat depende do banco; sem NAT não há Bedrock nem fila); senão, só perda de capacidade | `chat-sem-hosts-saudaveis`, `alb-5xx`, `rds-*`, eventos do RDS | tasks e instâncias espalhadas nas 2 zonas; ALB em 2 zonas | **proposta**: RDS Multi-AZ (failover automático em ~1–2 min) e uma NAT por zona (ou endpoints VPC para SQS, ECR, Logs, Secrets, Bedrock) |
| Banco falha ou dado corrompido/apagado | vendas param; risco de perder pedidos recentes | alarmes `rds-*`, `commerce-indisponivel`, erros nos logs | backup automático com restauração a qualquer ponto do último dia (PITR) | **manual**: restaurar o PITR numa instância nova e apontar o Terraform para ela; **proposta**: retenção de 7–35 dias, `deletion_protection`, snapshot final |
| Fila ou worker com problema | pedidos ficam PENDENTE, nada se perde | `fila-atrasada`, `dlq-com-mensagens`, `pedidos-travados` | outbox guarda o evento no banco; a fila retém 4 dias e a DLQ 14; reconciliação reenvia pedidos parados | `ops/dlq.sh reprocessar` depois de corrigir a causa |
| Bedrock fora ou com throttling | chat responde mensagem fixa; não vende nesse período | `llm-erros`, latência do LLM | perfil global do Bedrock distribui entre regiões; turno falho não é gravado | **proposta**: modelo alternativo de fallback |
| Deploy ruim / erro humano | versão nova não sobe | circuit breaker, alarmes, CloudTrail (`cloudtrail-mudancas-recentes`) | circuit breaker volta a revisão anterior | state versionado no S3; `terraform apply` da versão anterior |
| Abuso do `POST /chat` (robô, excesso de requisições) | custo de tokens no Bedrock; tasks ocupadas | métricas `ChatTurns`, `LLMCalls`, `LLMInputTokens` no dashboard; `RequestCount` do ALB | chat-api escala por requisições (até 6 tasks); limites de tamanho de mensagem e de iterações por turno | **proposta**: autenticação do cliente e WAF no ALB com limite por IP |
| **Região inteira fora** | plataforma fora | tudo em alarme / Health Dashboard | — | **manual**: `terraform apply` em outra região recria tudo em ~10 min (já provado em São Paulo), **mas sem os dados**; **proposta**: cópia automática dos backups do RDS para outra região e domínio próprio com failover de DNS (Route 53) |

**Resposta objetiva (encerramento):** o que temos hoje é **alta disponibilidade dentro da região** — falhas de task e
de instância se recuperam sozinhas, em minutos, sem perda de dados, com detecção por alarmes e diagnóstico por logs
correlacionados. **Recuperação de desastres completa ainda não**: a plataforma depende de uma zona (RDS Single-AZ e a
NAT) e não há cópia dos dados fora da região. A infraestrutura, porém, se reconstrói inteira pelo Terraform em ~10 min
em qualquer região; o que falta para um DR de verdade é levar os **dados** junto. Prioridades para uma startup real:
(1) RDS Multi-AZ com retenção maior e proteção contra exclusão; (2) NAT por zona ou endpoints VPC; (3) cópia de
backups entre regiões + domínio com failover — com RPO e RTO definidos.

## Checklist final contra as instruções

- [ ] 10 a 15 minutos; as seis etapas na ordem; todos os integrantes falam.
- [ ] Áudio claro; telas legíveis (zoom); nada de segredo, token, e-mail ou dado pessoal na tela.
- [ ] Demonstração real (sem simular): consulta a dados reais, pedido registrado e persistência comprovada.
- [ ] Diagrama fiel ao que foi criado; conexões, protocolos e dependências explicados.
- [ ] Console: recursos reais com configurações, não páginas iniciais.
- [ ] Decisões ligadas a requisitos e ao código do grupo, não definições genéricas.
- [ ] Etapa 6 termina com a resposta objetiva, separando alta disponibilidade de recuperação de desastres.
- [ ] Entrega no Blackboard: MP4 ou link que abre sem pedir permissão, nomes dos integrantes, código no GitHub
      **acessível ao professor** (o repositório hoje é privado).
