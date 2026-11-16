# Ensaio em São Paulo — 16/11/2026

Evidências, no ambiente final (código do commit `e25a0f2`, imagens `4efa7a282ee1`), dos itens que o enunciado pede
na demonstração final. Roteiro: `docs/roteiro-ensaio.md`. Ambiente religado com `ops/retomar.sh sa-east-1`.

| Item | Teste | Resultado | Log |
|---|---|---|---|
| Infraestrutura = código | `terraform plan -var-file=envs/sa-east-1.tfvars` com o ambiente no ar | **No changes** | `ensaio-terraform-plan.log` |
| MVP | `ops/demo.py` (8 frases) | kit R$ 889,64 → R$ 986,40; pedido criado no "Fechado!" e CONFIRMADO | `ensaio-demo.log` |
| Substituição de task | `ops/teste-task.sh` (para uma task do chat à força, com 2 req/s) | ECS iniciou outra em ~4 s, no ALB em ~40 s, serviço estável em ~70 s; **480 requisições, 100% 2xx** | `ensaio-task.log` |
| Perda de instância | `ops/teste-instancia.sh` (encerra a instância com 4 tasks, sem avisar ECS/ASG) + carga de 2 req/s | ASG lançou a substituta em **51 s**; serviços estáveis e 4/4 instâncias em **2 min 10 s**; 1.200 requisições, 17 respostas 502 (98,58%), 10 delas no minuto da queda | `ensaio-instancia.log`, `ensaio-instancia-carga.log` |
| Drenagem planejada | `ops/drenar-instancia.sh` + carga de 2 req/s | instância vazia em **65 s** (tasks movidas antes de parar); 840 requisições, 4 respostas 502 (99,52%) | `ensaio-drenagem.log`, `ensaio-drenagem-carga.log` |
| Persistência | `ops/teste-persistencia.sh` (pedido, troca de **todas** as tasks dos 3 serviços, mesma conversa) | 0 tasks sobreviveram; conversa com as mesmas 16 falas antes e depois; pedido continua CONFIRMADO | `ensaio-persistencia.log` |

## Achado: 502 esporádicos do ALB

Nenhum 5xx veio da aplicação (`HTTPCode_Target_5XX_Count` vazio); todos os 502 foram gerados pelo ALB. Além dos 10 no
minuto da perda de instância (esperados), houve 1–2 por vez minutos depois (13:49, 13:53, 13:56) e durante a drenagem,
sem atividade de escala nesses momentos. Causa provável: o uvicorn fecha conexões ociosas em **5 s** (padrão de
`timeout_keep_alive`) e o ALB as mantém por **60 s**; quando o ALB reaproveita uma conexão que a task acabou de fechar,
devolve 502. A recomendação da AWS é o servidor esperar mais que o balanceador (ex.: 65 s).
