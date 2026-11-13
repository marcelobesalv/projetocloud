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
