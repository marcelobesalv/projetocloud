# Roteiro: deploy da reconciliação e teste de carga em São Paulo

Objetivo da sessão (~2 h com o ambiente ligado):
1. levar o código novo (reconciliação de pedidos, migração 0002, alarme `pedidos-travados`) para sa-east-1;
2. medir a elasticidade **em São Paulo** com carga em degraus e gerar o relatório quantitativo (rubrica 5, A+);
3. demonstrar a reconciliação na AWS;
4. pausar de novo.

Onde roda cada parte:

| Parte | Onde | Por quê |
|---|---|---|
| Publicar as imagens | máquina local (Docker Desktop + AWS CLI com `aws sso login`) | o CloudShell não tem Docker |
| Religar, Terraform, teste da reconciliação, pausar | CloudShell de **sa-east-1** | Terraform e scripts bash; o `run-migrate.sh` do Terraform é bash |
| Carga e relatório | máquina local (só Python 3 + AWS CLI) | o CloudShell encerra a sessão depois de ~20 min sem digitar, e a carga dura 45 min |

Deixe o computador sem suspender durante a carga.

## 0. Antes de começar

- Acesso à conta `202757356229` pelo IAM Identity Center (permission set `AdministratorAccess`).
- Local: `aws sso login --profile <perfil>` e, no PowerShell, `$env:AWS_PROFILE = "<perfil>"`.
- `git pull` na `main` local: a tag da imagem é o commit atual (`git rev-parse --short HEAD`).

## 1. Publicar as imagens (local)

```powershell
.\push.ps1 sa-east-1 202757356229
```

Anote a tag impressa (o commit curto). O ECR é imutável: se a tag já existir, o push daquela imagem é recusado — tudo bem
se for a mesma versão.

## 2. Atualizar o código no CloudShell de sa-east-1

```bash
# Terraform >= 1.11 (a cada sessão do CloudShell; o /tmp é apagado)
curl -sLo /tmp/tf.zip https://releases.hashicorp.com/terraform/1.11.4/terraform_1.11.4_linux_amd64.zip
mkdir -p ~/bin && unzip -o /tmp/tf.zip -d ~/bin
export PATH=~/bin:$PATH TF_DATA_DIR=/tmp/tfdata AWS_REGION=sa-east-1 AWS_DEFAULT_REGION=sa-east-1

# Código: clone/pull do repositório e cópia para ~/tf e ~/ops (as pastas de trabalho do CloudShell)
git -C ~/projetocloud pull || git clone https://github.com/marcelobesalv/projetocloud ~/projetocloud
rsync -a --exclude .terraform ~/projetocloud/infra/terraform/ ~/tf/
rsync -a ~/projetocloud/ops/ ~/ops/ && chmod +x ~/ops/*.sh ~/tf/scripts/*.sh
```

Se o repositório for privado e o `git clone` pedir senha, use um token do GitHub ou envie um `.zip` do repositório pelo
menu **Actions → Upload file** do CloudShell.

## 3. Religar o ambiente (CloudShell)

```bash
~/ops/retomar.sh sa-east-1      # liga o RDS (5–10 min), ASG mínimo 2, serviços; termina com o status
```

## 4. Deploy da versão nova (CloudShell)

```bash
cd ~/tf
terraform init -backend-config=envs/sa-east-1.backend.hcl
terraform plan  -var-file=envs/sa-east-1.tfvars -var deploy_app=true -var image_tag=<tag> -out /tmp/plano
terraform apply /tmp/plano
```

O que o plano deve mostrar: novas revisões das 4 task definitions, `terraform_data.migrate` substituído (roda a migração
0002), os 3 serviços atualizados, 2 metric filters novos (`OrdersRequeued`, `OrdersStuck`), o alarme
`setup-pronto-pedidos-travados` e o dashboard alterado. **Se aparecer destroy de RDS, VPC, ALB ou fila, pare e investigue.**

Conferir:

```bash
~/ops/status.sh sa-east-1                              # 3 serviços COMPLETED, nada em ALARM além do esperado
python3 ~/ops/demo.py "$(terraform output -raw url)"   # roteiro completo (pedido CONFIRMADO no fim)
aws logs filter-log-events --log-group-name /ecs/setup-pronto/commerce-api \
  --filter-pattern '{ $.event = "reconciliation_cycle" }' --max-items 2 --query 'events[].message' --output text
```

O último comando deve mostrar ciclos com `"reenviados": 0, "travados": 0` (um por minuto). Se o chat responder
`commerce_indisponivel` logo depois do deploy: `aws ecs update-service --cluster setup-pronto-cluster --service chat-api --force-new-deployment`.

## 5. Teste de carga em degraus (local, 45 min)

Plano: a política do chat-api é **30 req/min por task** (0,5 req/s por task), mínimo 2 e máximo 6 tasks.

| Degrau | Carga | Duração | Esperado (aproximado; é isso que o teste mede) |
|---|---|---|---|
| 1 | 0,5 req/s (30/min) | 5 min | linha de base: 2 tasks, 2 instâncias |
| 2 | 2 req/s (120/min) | 10 min | chat sobe para 4 tasks; cabe nas 2 instâncias |
| 3 | 4 req/s (240/min) | 10 min | chat no máximo (6); a CPU reservada passa de 2 instâncias → capacity provider sobe para 3 |
| 4 | 0,5 req/s | 20 min | scale-in: o target tracking reduz tasks após ~15 min abaixo do alvo; depois o capacity provider remove a instância vazia |

```powershell
python ops/carga.py http://<dns do ALB> --degraus 0.5:300,2:600,4:600,0.5:1200 --chat-cada 10 `
  --csv docs/resultados/carga-sp-cliente.csv > docs/resultados/carga-sp.log
```

`--chat-cada 10` faz um turno com o Bedrock a cada 10 s (~270 turnos), para a carga incluir o caminho real
chat → LLM → commerce. Enquanto roda, no CloudShell: `watch -n 30 ~/ops/status.sh sa-east-1` e o dashboard
`setup-pronto` no CloudWatch — **tire prints** dos gráficos "Tasks em execucao", "Instancias do ASG" e "Reserva do cluster"
para os slides.

## 6. Relatório (local, 5 min depois do fim da carga)

```powershell
python ops/metricas.py --regiao sa-east-1 --carga docs/resultados/carga-sp-cliente.csv --folga-min 35 --saida docs/resultados/relatorio-sp
```

Gera `docs/resultados/relatorio-sp.{csv,md,html}`: por minuto (tasks, instâncias, requisições, 5xx, latência, reserva),
resumo por degrau, tempo de reação do auto scaling e as atividades de escala. O `.html` tem os gráficos. **Use um nome de
saída diferente do CSV da carga** (o script recusa se forem iguais). A folga cobre o scale-in: as tasks começam a cair
~15 min depois de a carga baixar e a instância vazia sai ~15 min depois disso; rode quando o ASG voltar à base.
Commite tudo o que estiver em `docs/resultados/`.

No Windows (Git Bash): o `python3` costuma ser o atalho da Microsoft Store, os `.sh` precisam de LF (o `.gitattributes`
garante isso em checkouts novos) e o `docker login` no ECR pode falhar no gerenciador de credenciais do Windows
("The stub received bad data"); nesse caso, rode as partes de bash no CloudShell.

Números para levar à apresentação: disponibilidade (1 − 5xx/requisições), tempo de reação de tasks e de instâncias
em cada degrau, p90 por degrau e requisições por task comparadas ao alvo de 30/min.

## 7. Reconciliação na AWS (CloudShell, ~15 min)

```bash
~/ops/teste-reconciliacao.sh sa-east-1
```

Desliga o order-worker, faz um pedido pelo chat, purga a fila (a mensagem se perde), religa o worker e espera: em ~11 min
o commerce-api reenvia (`order_requeued`) e o worker confirma (`order_processed`), com o mesmo `correlation_id`. O alarme
`order-worker-tasks-abaixo-do-minimo` dispara durante o teste — é esperado. Guarde a saída do script.

Opcional: `~/ops/dlq.sh sa-east-1 ver` (a DLQ deve estar vazia).

## 8. Pausar (CloudShell)

```bash
~/ops/pausar.sh sa-east-1
~/ops/status.sh sa-east-1     # tasks 0, ASG mínimo 0, RDS parando
```

## 9. Registrar

Atualizar no `CLAUDE.md` (seção "Estado da implantação"): a tag em uso em São Paulo, a data e um resumo dos resultados
(tasks de → para, instâncias de → para, tempos de reação, 5xx).
