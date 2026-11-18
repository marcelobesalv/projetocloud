# Runbook de incidentes — Setup Pronto

O que fazer quando um alarme dispara. Vale para São Paulo (Terraform) e Ohio (manual); os nomes dos recursos são os
mesmos nas duas regiões, só muda a região. Todo alarme tem o prefixo `setup-pronto-` e notifica o tópico SNS
`setup-pronto-alertas` (com `alarm_email` no Terraform, chega por e-mail).

## 1. Ferramentas

| Ferramenta | Onde | Para quê |
|---|---|---|
| Dashboard `setup-pronto` | CloudWatch → Dashboards | ALB (requisições, 5xx, latência, hosts saudáveis), CPU/memória por serviço, fila e DLQ, pedidos, LLM, RDS, ASG, reserva do cluster, tasks, segurança e o quadro de alarmes |
| Consultas salvas | CloudWatch → Logs Insights → Consultas salvas → `setup-pronto/` | ver a lista abaixo |
| `ops/status.sh <regiao>` | terminal (CloudShell ou AWS CLI local) | foto do ambiente: serviços, ASG, instâncias, fila, DLQ, alarmes em ALARM e `/health` |
| `ops/dlq.sh <regiao> ver\|reprocessar` | terminal | inspecionar e devolver mensagens da DLQ para a fila |
| `correlation_id` | resposta do `POST /chat`, header `X-Correlation-ID`, `pedidos.correlation_id`, atributo da mensagem SQS | seguir uma conversa/pedido pelos três serviços |
| Trilha do CloudTrail | S3 `setup-pronto-cloudtrail-<conta>-<regiao>` e log group `/setup-pronto/cloudtrail` | quem fez o quê na conta (todas as regiões) |

Consultas salvas (Logs Insights):

| Consulta | Use quando |
|---|---|
| `pedido-por-correlation-id` | um cliente relata problema numa conversa ou pedido (troque `COLE_O_CORRELATION_ID_AQUI`) |
| `erros-e-avisos-por-servico` | qualquer alarme de aplicação: mostra quais eventos de erro estão acontecendo e onde |
| `pedidos-reenviados-e-travados` | `pedidos-travados`, `fila-atrasada`, `dlq-com-mensagens` |
| `mensagens-problematicas-da-fila` | `dlq-com-mensagens`, `fila-atrasada` |
| `llm-latencia-e-falhas-por-minuto` | `llm-erros`, `chat-latencia-p90` |
| `cloudtrail-acesso-negado` | `seg-acesso-negado`, ou um serviço falhando com AccessDenied |
| `cloudtrail-mudancas-recentes` | qualquer incidente: o que mudou na conta pouco antes (deploy, SG, IAM, scale) |

Eventos de log úteis (campo `event`, JSON no stdout de cada serviço): `chat_turn`, `llm_call`, `tool_call`,
`tool_loop_limit`, `commerce_unavailable`, `commerce_transport_error`, `order_created`, `order_idempotent_hit`,
`outbox_published`, `outbox_publish_failed`, `outbox_cycle_error`, `order_requeued`, `reconciliation_cycle`,
`reconciliation_error`, `order_processed`, `order_transient_error`, `order_not_found`, `poison_message`,
`sqs_receive_failed`, `sqs_delete_failed`, `unhandled_error`.

## 2. Processo

1. **Reconhecer**: quem viu o alarme avisa no grupo ("olhando o `<alarme>`") para não haver duas pessoas mexendo.
2. **Classificar**:
   - **S1** — cliente não consegue conversar ou fechar pedido (`chat-sem-hosts-saudaveis`, `alb-5xx` alto,
     `commerce-indisponivel` contínuo, `asg-abaixo-do-minimo` com tasks abaixo do mínimo).
   - **S2** — funciona degradado ou com atraso (`llm-erros`, `chat-latencia-p90`, `fila-atrasada`, `pedidos-travados`,
     `capacidade-insuficiente`).
   - **S3** — sem impacto imediato (reservas altas, `rds-cpu`, `rds-disco`, alarmes de segurança esperados).
3. **Conter** antes de investigar a fundo (ações rápidas em cada seção abaixo). Nada de apagar dados: pedidos, estoque e
   conversas estão no RDS; filas têm retenção.
4. **Diagnosticar** com o dashboard, `ops/status.sh` e as consultas salvas; sempre olhar `cloudtrail-mudancas-recentes`
   (a maioria dos incidentes vem de uma mudança).
5. **Resolver** e confirmar que o alarme voltou a OK.
6. **Registrar** no modelo da seção 5 (`docs/incidentes/AAAA-MM-DD-<titulo>.md`).

## 3. Alarmes

### Disponibilidade do chat (porta de entrada)

**`chat-sem-hosts-saudaveis`** — alguma task do chat-api falha o health check do ALB por 3 min.
- Diagnóstico: `ops/status.sh` (running < desired? deployment em `IN_PROGRESS`/`FAILED`?); ECS → serviço chat-api →
  Eventos (motivo de parada das tasks); `erros-e-avisos-por-servico`. O `/health` do chat checa **só o banco**: unhealthy
  quase sempre é RDS fora/lotado ou a task sem memória (`OutOfMemoryError` no motivo de parada).
- Ação: o ECS substitui a task sozinho; se o deploy novo é que está falhando, o circuit breaker volta a revisão anterior.
  Se for o banco, ver `rds-cpu`/RDS → eventos. Testado: task parada à força é reposta sem erro para o cliente.

**`alb-5xx`** — mais de 5 respostas 5xx das tasks do chat em 5 min.
- Diagnóstico: separar 5xx da task (`HTTPCode_Target_5XX_Count`, bug ou dependência) de 502/503 do ALB
  (`HTTPCode_ELB_5XX_Count`, task saindo/entrando). `erros-e-avisos-por-servico` filtrando `unhandled_error`.
- Ação: 502 isolados durante scale-out ou perda de instância são esperados (medido: 3 em 4.622 requisições no
  scale-out de 12/11; ~1% por ~10 s ao perder uma instância). Se forem contínuos, ver o deploy mais recente
  (`cloudtrail-mudancas-recentes`, `UpdateService`) e reverter para a revisão anterior.

**`chat-latencia-p90`** — p90 do ALB acima de 30 s.
- Diagnóstico: `llm-latencia-e-falhas-por-minuto` (Bedrock lento ou com throttling?), `ops/status.sh` (tasks no
  máximo?), CPU por serviço no dashboard. Atenção: o p90 mistura páginas e turnos do chat; com pouco tráfego, os turnos
  (~4–6 s) dominam.
- Ação: se é o LLM, nada a escalar do nosso lado (o turno tem orçamento de 50 s, abaixo do idle timeout de 60 s do ALB);
  se é CPU, conferir se o auto scaling está no máximo (`asg-no-maximo`).

**`<serviço>-tasks-abaixo-do-minimo`** — chat-api, commerce-api ou order-worker com menos tasks que o mínimo por 3 min.
- Diagnóstico: ECS → serviço → Eventos (`unable to place a task`? → capacidade; task parando em loop? → logs da task).
- Ação: falta de capacidade → ver `capacidade-insuficiente`; loop de falha → logs e revisão anterior. Disparar com o
  ambiente **pausado** é esperado.

**`commerce-indisponivel`** — o chat-api não conseguiu falar com o commerce-api (rede, timeout ou 5xx).
- Impacto: o chat responde a mensagem fixa "não consegui consultar agora" e o turno não é gravado — nada inconsistente.
- Diagnóstico: `ops/status.sh` (commerce rodando?); logs do commerce-api; se o commerce está saudável mas o chat não o
  alcança logo depois de criar os serviços, é o alias do Service Connect que não existia quando a task do chat subiu.
- Ação: `aws ecs update-service --cluster setup-pronto-cluster --service chat-api --force-new-deployment`. Testado:
  commerce desligado → resposta fixa + alarme; religado → volta sozinho.

**`llm-erros`** — 3 ou mais falhas do Bedrock em 5 min.
- Diagnóstico: `llm-latencia-e-falhas-por-minuto`; campos `error_type`/`error` do `llm_call` com `success = false`
  (`ThrottlingException`, `AccessDeniedException`, timeout); `cloudtrail-acesso-negado` se for permissão (inference profile ou modelo em outra região do perfil).
- Ação: throttling → esperar/reduzir carga; AccessDenied → conferir a policy do `chat-api-task-role` (perfil e ARNs de
  modelo das regiões do perfil). O cliente recebe a resposta fixa; nenhum pedido fica pela metade.

### Pedidos e mensageria

**`dlq-com-mensagens`** — há mensagem na `setup-pronto-orders-dlq` (5 recebimentos sem sucesso).
- Diagnóstico: `ops/dlq.sh <regiao> ver` e `mensagens-problematicas-da-fila`: `poison_message` = JSON inválido;
  `order_not_found` = pedido inexistente; `order_transient_error` repetido = banco fora por mais de ~5 min.
- Ação: corrigir a causa e `ops/dlq.sh <regiao> reprocessar` (o worker ignora pedidos já resolvidos). Mensagem inválida
  não tem conserto: registrar e purgar a DLQ. Pedidos válidos que caíram na DLQ já voltam sozinhos pela reconciliação.
  Testado: mensagem inválida → DLQ após 5 tentativas + alarme.

**`fila-atrasada`** — a mensagem mais antiga da fila `orders` tem mais de 5 min.
- Diagnóstico: `ops/status.sh` (order-worker rodando?); `mensagens-problematicas-da-fila` (`order_transient_error`?);
  RDS disponível?
- Ação: worker parado → o ECS/auto scaling repõe (mínimo 1); banco fora → resolver o banco, as mensagens voltam pela
  visibility timeout. Não purgar a fila.

**`pedidos-travados`** — pedidos PENDENTE que continuaram parados mesmo depois dos reenvios da reconciliação
(`RECONCILIACAO_MAX_REENVIOS`, padrão 3).
- Diagnóstico: `pedidos-reenviados-e-travados` (quais pedidos e quantos reenvios); para cada um,
  `pedido-por-correlation-id`; `dlq-com-mensagens` e `fila-atrasada` costumam disparar junto.
- Ação: a causa é permanente (worker sem permissão na fila, banco recusando o UPDATE, mensagem indo sempre para a DLQ).
  Corrigida a causa, reprocessar a DLQ; se o pedido não puder ser atendido, registrar no incidente.
  Testado em 12/11: pedido com a mensagem purgada → reenviado em ~10 min → CONFIRMADO, mesmo `correlation_id`.

### Capacidade do cluster

**`cluster-cpu-reservada` / `cluster-memoria-reservada`** — mais de 90% reservado por 15 min.
- Diagnóstico: dashboard "Reserva do cluster" e "Instancias do ASG"; o capacity provider mira 90%, então valores
  perto disso são normais; o alarme indica que ele **não** está conseguindo abrir folga.
- Ação: ver `asg-no-maximo` e `capacidade-insuficiente`.

**`capacidade-insuficiente`** — `CapacityProviderReservation` acima de 100% por 15 min: há tasks esperando instância.
- Diagnóstico: ECS → serviço → Eventos (`unable to place a task because no container instance met all of its
  requirements`); ASG → Atividades (falha ao lançar? cota de vCPU? AMI?).
- Ação: se o ASG está no máximo, subir `asg_max` (Terraform) ou reduzir o `max` do auto scaling das tasks; se o ASG não
  consegue lançar, corrigir a causa da atividade com falha.

**`asg-abaixo-do-minimo`** — instâncias em serviço abaixo do mínimo por 5 min.
- Diagnóstico: ASG → Atividades e Gerenciamento de instâncias (instância `Unhealthy`/`Terminating`?).
- Ação: o ASG repõe sozinho (medido: ~1 min para repor após perda abrupta). Se a reposição falha, ver a atividade.
  Disparar com o ambiente pausado é esperado.

**`asg-no-maximo`** — ASG no máximo por 15 min: não há folga para escalar.
- Ação: decidir se é pico legítimo (subir `asg_max`) ou tasks desnecessárias (conferir o `max` do auto scaling de
  cada serviço). Depois de um deploy a frota sobe ao máximo por ~15 min (tasks em dobro): esperado.

### Banco

**`rds-cpu`** — CPU do RDS acima de 80% por 15 min. **`rds-disco`** — menos de 2 GB livres.
- Diagnóstico: RDS → Monitoramento e Performance Insights (se ligado); `erros-e-avisos-por-servico` (timeouts de
  statement?); tamanho de `conversas` (o histórico cresce sem limite).
- Ação: CPU → identificar a consulta; disco → aumentar o armazenamento (`allocated_storage` em `infra/terraform/data.tf`, hoje 20 GB) e limpar
  conversas antigas.

### Segurança (CloudTrail)

Os eventos chegam ao CloudWatch Logs com 5–15 min de atraso.

**`seg-mudanca-security-group` / `seg-mudanca-iam`** — alteração de security group ou de IAM.
- Esperado durante `terraform apply` ou nos scripts da etapa manual. Fora disso: `cloudtrail-mudancas-recentes` para
  ver quem (`userIdentity.arn`), o quê e de onde; se não foi o grupo, desfazer pelo Terraform (`terraform plan` mostra a
  diferença) e trocar credenciais.

**`seg-acesso-negado`** — 5 ou mais chamadas negadas em 5 min.
- Diagnóstico: `cloudtrail-acesso-negado`. Se o `userIdentity.arn` é uma role de task, é permissão faltando (menor
  privilégio apertado demais) e o serviço está falhando — conferir `iam.tf`. Se é um usuário, pode ser tentativa
  indevida ou alguém sem o permission set certo.

**`seg-uso-root`** — o usuário root da conta foi usado. Ninguém do grupo deve usar root: tratar como S1, avisar o
professor/responsável pela conta.

**`seg-mudanca-cloudtrail`** — trilha criada, alterada, parada ou removida. Fora de um `terraform apply`, é tentativa de
apagar rastro: tratar como S1 e conferir a integridade com
`aws cloudtrail validate-logs --trail-arn <arn> --start-time <início>`.

## 4. Cenários já exercitados

| Cenário | Como provocar | Resultado medido |
|---|---|---|
| Task defeituosa | `aws ecs stop-task` numa task do chat | reposta pelo ECS, sem erro para o cliente (Ohio, 03/11) |
| Perda abrupta de instância | `ops/teste-instancia.sh` | ASG repõe em ~1 min; ~1% de 502 por ~10 s (SP, 04/11) |
| Drenagem planejada | `ops/drenar-instancia.sh` | sem erros (SP, 04/11) |
| commerce-api fora | parar o serviço | resposta fixa no chat, turno não gravado, alarme `commerce-indisponivel` (Ohio) |
| Mensagem inválida | enviar JSON quebrado na fila | DLQ após 5 recebimentos, alarme `dlq-com-mensagens` (Ohio) |
| Mensagem de pedido perdida | `ops/teste-reconciliacao.sh` | reenvio em ~10 min, pedido CONFIRMADO, mesmo `correlation_id` (SP, 12/11) |
| Carga em degraus | `ops/carga.py --degraus` + `ops/metricas.py` | 2→6 tasks, 3→4 instâncias, 99,935% (SP, 12/11; `docs/resultados/relatorio-sp.md`) |

## 5. Registro do incidente (modelo)

```markdown
# <data> — <título curto>
- Severidade: S1 | S2 | S3          - Região:
- Detectado por: <alarme / pessoa>  - Início → fim (UTC):
- Impacto: <o que o cliente viu; quantas requisições/pedidos afetados>

## Linha do tempo (UTC)
- hh:mm alarme X disparou
- hh:mm ...

## Causa
<o que aconteceu e por quê; evidências: consultas, prints, correlation_ids>

## Correção
<o que foi feito para resolver>

## Para não repetir
- [ ] <ação> — responsável — prazo
```
