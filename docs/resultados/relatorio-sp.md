# Teste de carga — sa-east-1

Janela: 2026-11-12T19:18:00Z a 2026-11-12T20:38:07Z (UTC), 80 minutos de métricas.

## Totais (lado do ALB)

- Requisições: **4622**; respostas 5xx: **3** (disponibilidade 99.935%)
- Tasks do chat-api: 2 → 6; instâncias: 3 → 4
- Latência p90 do ALB: mediana dos minutos 3818 ms, pior minuto 4683 ms
- DLQ no fim: 0

## Por degrau

| degrau | alvo (req/s) | duração | req/min no ALB | req/min por task | tasks chat (máx) | instâncias (máx) | CPU reservada (máx) | 5xx ALB | p90 ALB (ms) | cliente: 2xx % | cliente: erros |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.5 | 5 min | 48 | 24 | 2 | 3 | 38% | 0 | 3656 | 100.00 | 0 |
| 2 | 2.0 | 10 min | 134 | 47 | 6 | 4 | 62% | 3 | 8 | 99.75 | 3 |
| 3 | 4.0 | 10 min | 228 | 38 | 6 | 4 | 53% | 0 | 12 | 100.00 | 0 |
| 4 | 0.5 | 20 min | 36 | 6 | 6 | 4 | 53% | 0 | 3967 | 100.00 | 0 |

O p90 do ALB mistura `GET /` (dezenas de ms) com `POST /chat` (segundos: chamadas ao LLM). Com carga baixa os turnos do chat passam de 10% das requisições e dominam o p90; a latência das páginas por degrau está no resumo do `ops/carga.py` (lado do cliente).

### Tempo de reação

Contado do início de cada degrau, pelas tasks do chat-api e instâncias **em execução** (resolução de 1 min): quando o número mudou pela primeira vez e quando chegou ao máximo do degrau (no degrau de descida, ao mínimo). O último degrau inclui a folga depois da carga, onde acontece o scale-in.

| degrau | tasks: de → para | 1ª mudança | completo | instâncias: de → para | 1ª mudança | completo |
|---|---|---|---|---|---|---|
| 1 | 2 | - | - | 3 | - | - |
| 2 | 2 → 6 | 7 min | 10 min | 3 → 4 | 7 min | 7 min |
| 3 | 6 | - | - | 4 | - | - |
| 4 | 6 → 2 | 18 min | 36 min | 4 → 3 | 48 min | 48 min |

## Atividades de escala

- `19:30:13` **chat-api** (Successful): Setting desired count to 5.
- `19:32:36` **ASG** (Successful): Launching a new EC2 instance: i-06a5e00d5b53825d4
- `19:33:13` **chat-api** (Successful): Setting desired count to 6.
- `20:02:29` **chat-api** (Successful): Setting desired count to 5.
- `20:08:29` **chat-api** (Successful): Setting desired count to 4.
- `20:14:29` **chat-api** (Successful): Setting desired count to 3.
- `20:20:29` **chat-api** (Successful): Setting desired count to 2.
- `20:32:30` **ASG** (Cancelled): Could not scale to desired capacity because all remaining instances are protected from scale-in.
- `20:33:12` **ASG** (Successful): Terminating EC2 instance: i-00f021078c6817546

Séries por minuto: `docs/resultados/relatorio-sp.csv`. Gráficos: `docs/resultados/relatorio-sp.html`.
