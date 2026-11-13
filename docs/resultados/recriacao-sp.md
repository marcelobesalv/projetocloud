# Recriação de São Paulo do zero — 13/11/2026

Objetivo: provar que o ambiente completo (com a borda e a auditoria acrescentadas depois de 04/11) é criado e
destruído só a partir do código, sem passos manuais, e deixar rodando a versão final (`52bc501`).

Feito de um Windows com a AWS CLI (SSO) e o Terraform 1.11.4 num container; state remoto no S3.

| Etapa | Comando | Resultado | Duração |
|---|---|---|---|
| Destroy | `terraform destroy` (com `deploy_app=true`) | 159 recursos removidos, sem erro | 6 min (16:24–16:30 UTC) |
| Conferência | VPC, RDS, SQS, CloudFront, trilha, task definitions ativas | nada restante; só o bucket do state (do `bootstrap/`) | — |
| Fase 1 | `terraform apply` (rede, RDS, ECR, IAM, cluster, ALB, borda, auditoria, observabilidade) | 145 recursos | 8 min |
| Imagens | build + push da `52bc501` no ECR novo | 4 imagens | ~2 min |
| Fase 2 | `terraform apply -var deploy_app=true -var image_tag=52bc501` | 14 recursos; migração `exitCode=0`, seed com 33 produtos e 50 variantes | 39 s |
| Serviços estáveis | `aws ecs wait services-stable` | 3 serviços no ar | ~2,5 min |

As duas fases criaram 145 + 14 = **159 recursos, exatamente os 159 removidos** pelo destroy.

## Testes no ambiente recriado

| Teste | Resultado |
|---|---|
| Demonstração completa (`ops/demo.py`) pela URL nova `https://d36s1ot5jejh6l.cloudfront.net/` | kit R$ 889,64 → R$ 986,40; pedido criado no turno do "Fechado! Pode fazer o pedido" (prompt novo) e CONFIRMADO pelo worker (`demo-sp-recriado.log`) |
| Acesso direto ao ALB | sem resposta (o SG só aceita o CloudFront) |
| Limite do WAF (`POST /chat` inválidos) | 429 a partir da 160ª requisição |
| CloudTrail | gravando, sem erro de entrega |
| Alarmes | 23 criados, nenhum em ALARM |

Ambiente pausado em seguida (`ops/pausar.sh sa-east-1`).

# Um único `terraform apply` do zero — 13/11/2026 (tarde)

Depois da entrega do enunciado do vídeo ("ao executar o código ele deve criar a infraestrutura completa, sem nenhuma
intervenção manual"), o build das imagens passou para dentro do Terraform (`infra/terraform/build.tf`: código no S3 →
CodeBuild → ECR da região) e as duas fases viraram um apply só.

| Etapa | Comando | Resultado | Duração |
|---|---|---|---|
| Destroy | `terraform destroy -var-file=envs/sa-east-1.tfvars` | 164 recursos; state vazio (0) | 4 min (18:43–18:47 UTC) |
| **Apply único** | `terraform apply -var-file=envs/sa-east-1.tfvars` | **173 recursos**, imagens `4efa7a282ee1` construídas pelo CodeBuild, migração `exitCode=0`, seed com 33 produtos | **9 min** (18:47–18:56 UTC) |
| Aplicação | `ops/demo.py` na URL nova `https://d29glptl5pxvls.cloudfront.net/` | pedido criado no "Fechado!" e CONFIRMADO (`demo-apply-unico.log`) | — |

Problemas encontrados no caminho e corrigidos no código:
- **Propagação do IAM**: o CodeBuild e a task de migração rodam segundos depois de as roles/políticas serem criadas; a
  primeira tentativa falha com "not authorized". `scripts/build-imagens.sh` e `scripts/run-migrate.sh` repetem esse
  caso (e só esse) com espera — no apply final cada um precisou de uma segunda tentativa.
- **Queda de rede local no meio de um destroy**: o Terraform gravou `errored.tfstate`; recuperado com
  `terraform force-unlock` (a trava era do próprio destroy interrompido) e `terraform state push errored.tfstate`.
