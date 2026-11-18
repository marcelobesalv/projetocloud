# Roteiro de ensaio e da demonstração final — São Paulo

O enunciado (seção 5) pede que a apresentação final demonstre: **o funcionamento do MVP, o escalonamento das tasks, o
escalonamento das instâncias EC2, a substituição de componentes defeituosos, a persistência dos dados e a criação
completa da infraestrutura em São Paulo.** Este roteiro gera a evidência de cada item no ambiente final e serve de
ensaio da demonstração ao vivo. Cada passo grava a saída em `docs/resultados/ensaio-*.log`; commite tudo no fim.

Duração do ensaio completo: ~1 h 30 com o ambiente ligado. Na apresentação, os passos marcados **(ao vivo)** cabem em
~15 min; o resto entra como resultado já medido (relatórios e logs).

Onde rodar: Git Bash (Windows) ou CloudShell de sa-east-1, na raiz do repositório, com a AWS CLI autenticada
(`aws sso login --profile setup-pronto` e `export AWS_PROFILE=setup-pronto`). Os scripts acham sozinhos a URL do
ALB e o Python certo (`ops/comum.sh`).

## 0. Antes (no dia anterior e no dia)

- [ ] **RDS**: parado em 13/11, ele religa sozinho em 20/11 (limite de 7 dias da AWS). Confira o estado.
- [ ] Religar: `bash ops/retomar.sh sa-east-1` (liga o RDS, ASG 2–4, limites das tasks, commerce antes do chat).
- [ ] **Esperar a frota estabilizar em 3 instâncias** (~15 min depois de subir: a religada sobe a frota ao máximo):
      `watch -n 30 bash ops/status.sh sa-east-1`. Medir com a frota em 4 contamina a carga.
- [ ] `bash ops/dlq.sh sa-east-1 ver` → DLQ vazia. Nenhum alarme em ALARM além dos `seg-*` de mudanças recentes.
- [ ] Abas abertas: URL do chat, dashboard `setup-pronto`, ECS → cluster, EC2 → Auto Scaling Groups, Logs Insights,
      `docs/arquitetura.png`, `docs/resultados/relatorio-sp.html`.

## 1. MVP funcionando (ao vivo, ~3 min)

Rubrica 1. Na página do ALB (`terraform output url`) (ou `bash -c 'cd ops && source comum.sh && $PY demo.py $URL'`):
o roteiro de 8 frases (busca → compatibilidade → kit de R$ 889,64 → pedido de desconto → mousepad, R$ 986,40 →
"Fechado! Pode fazer o pedido no nome de Ana Souza." → CONFIRMADO).

Mostrar: a IA não inventa desconto (frase 4); o pedido nasce PENDENTE e o worker confirma; o `correlation_id` da
resposta na consulta salva `setup-pronto/pedido-por-correlation-id` mostra o pedido passando pelos três serviços.

```bash
cd ops && source comum.sh sa-east-1 && $PY demo.py $URL | tee ../docs/resultados/ensaio-demo.log; cd ..
```

## 2. Escalonamento das tasks e das instâncias (~15 min; ao vivo só o começo)

Rubricas 4 e 5. Uma carga curta que leva o chat a 6 tasks e a frota a 4 instâncias:

```bash
cd ops && source comum.sh sa-east-1 && $PY carga.py $URL --degraus 4:900 --chat-cada 10 \
  --csv ../docs/resultados/ensaio-carga.csv | tee ../docs/resultados/ensaio-carga.log; cd ..
```

Mostrar no dashboard: "Tasks em execucao" (chat 2 → 6 em ~10 min), "Instancias do ASG" (3 → 4) e "Reserva do
cluster". Explicar: a política do chat mira 30 req/min por task; as tasks reservam CPU e memória, e quando não cabem
o capacity provider (alvo 90%) sobe instância. Na apresentação, use o relatório de 12/11 (`relatorio-sp.html`) para
os números completos, inclusive o scale-in com a termination protection liberando só a instância vazia.

## 3. Substituição de componentes defeituosos

Rubricas 3 e 4. Rode depois que a carga do passo 2 acabar e a frota estabilizar.

**Task defeituosa (ao vivo, ~4 min)**: para uma task do chat à força; o ECS sobe outra e o ALB tira a antiga.

```bash
bash ops/teste-task.sh sa-east-1 chat-api | tee docs/resultados/ensaio-task.log
```

**Perda de instância (~10 min)**: encerra a instância com mais tasks, sem avisar o ECS nem o ASG; o ASG repõe e o ECS
recoloca as tasks. Rode uma carga leve junto para medir o impacto (em 04/11: ~1% de 502 por ~10 s).

```bash
(cd ops && source comum.sh sa-east-1 && $PY carga.py $URL --degraus 2:600 --chat-cada 0 --janela 15 \
  > ../docs/resultados/ensaio-instancia-carga.log) &
bash ops/teste-instancia.sh sa-east-1 | tee docs/resultados/ensaio-instancia.log; wait
```

**Drenagem planejada (~5 min)**: a instância vai a DRAINING, o ECS move as tasks antes de pará-las; sem erros.

```bash
bash ops/drenar-instancia.sh sa-east-1 | tee docs/resultados/ensaio-drenagem.log
```

## 4. Persistência dos dados (ao vivo, ~8 min)

Rubrica 1 e requisito "sem perda dos dados persistentes". Faz um pedido, troca **todas** as tasks dos três serviços e
confere, na mesma conversa, que o histórico e o pedido continuam lá (tudo está no RDS, nada na task).

```bash
bash ops/teste-persistencia.sh sa-east-1 | tee docs/resultados/ensaio-persistencia.log
```

Esperado: "tasks que sobreviveram à troca: 0", o mesmo número de falas antes e depois, e o chat respondendo que o
pedido está CONFIRMADO.

## 5. Falhas da aplicação e mensageria (resultados já medidos)

Rubricas 1 e 5. Não precisa repetir; mostrar os logs:
- reconciliação de pedido com mensagem perdida: `docs/resultados/reconciliacao-sp.log`;
- DLQ e commerce fora do ar: `docs/runbook.md`, seção 4 (cenários já exercitados).

Opcional ao vivo (~2 min): `aws ecs update-service --cluster setup-pronto-cluster --service commerce-api
--desired-count 0`, uma pergunta no chat (resposta fixa, nada inventado, alarme `commerce-indisponivel`) e
`--desired-count 2` para voltar.

## 6. Segurança e operação (ao vivo, ~3 min)

Rubrica 6:
- instâncias sem IP público e SGs encadeados (internet → ALB → instâncias → RDS);
- CloudTrail: consulta salva `setup-pronto/cloudtrail-mudancas-recentes` mostrando o que foi feito no ensaio;
- runbook: escolher um alarme do dashboard e mostrar a seção correspondente em `docs/runbook.md`.

## 7. Criação completa em São Paulo (resultado já medido + prova ao vivo)

Rubrica 7. Mostrar `docs/resultados/recriacao-sp.md` (destroy de 159 recursos e recriação dos mesmos 159, sem passo
manual). Ao vivo, provar que o que está rodando é exatamente o código: `terraform plan` deve dizer **No changes**
(rode antes do passo 2, com o ambiente estável; o auto scaling muda contagens, que o Terraform ignora):

```bash
cd infra/terraform && terraform plan -var-file=envs/sa-east-1.tfvars
```

(Terraform configurado como no `infra/terraform/README.md`. Única diferença legítima: se a AWS publicou uma AMI do ECS
nova desde o apply, o plano mostra uma versão nova do launch template — é o parâmetro SSM funcionando.)

Portabilidade: `grep -rn us-east-2 --include=*.tf --include=*.tfvars --include=*.hcl infra/terraform` não encontra
nada; AMI (parâmetro SSM), zonas (data source) e imagens (ECR da própria região) são resolvidos em São Paulo. Nenhum
recurso fica fora de sa-east-1 (a trilha do CloudTrail é multirregião, mas criada em São Paulo).

## 8. Depois

```bash
bash ops/pausar.sh sa-east-1
git add docs/resultados && git commit -m "Evidências do ensaio em São Paulo"
```

## Pontos para defender nos slides (rubricas A/A+)

- **Custo × disponibilidade**: RDS Single-AZ e uma NAT só (uma zona inteira fora derruba o sistema); com mais
  orçamento, Multi-AZ e uma NAT por zona. Entrada em HTTP porque não há domínio próprio para o certificado.
- **502 residuais**: 3 em 4.622 requisições no scale-out e ~1% por ~10 s numa perda abrupta de instância; a drenagem
  planejada não gera erro.
- **Dimensionamento**: base de 3 instâncias (2 necessárias ÷ alvo de 90%); tasks com reserva e limite de CPU e
  memória; placement espalha entre zonas e depois faz binpack por memória.
- **Decomposição**: chat (público, escala por requisições, depende do LLM), commerce (interno, escala por CPU, dono dos
  dados de venda), worker (assíncrono, escala pela fila): falha de um não derruba os outros.
