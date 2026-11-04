#!/bin/bash
# Bloco 4: task definitions + job de migração
set -euo pipefail
source ~/sp.env
P=setup-pronto; R=us-east-2; ACC=202757356229; C=$P-cluster
TAG=${TAG:-37791f6}
REG=$ACC.dkr.ecr.$R.amazonaws.com
DB_HOST=$(aws rds describe-db-instances --db-instance-identifier $P-db --query 'DBInstances[0].Endpoint.Address' --output text)
export P R ACC TAG REG DB_HOST Q_URL SEC_CHAT_API SEC_COMMERCE_API SEC_ORDER_WORKER RDS_SECRET
mkdir -p ~/td
python3 - <<'PY'
import json, os
e = os.environ
P, R, ACC, TAG, REG = e["P"], e["R"], e["ACC"], e["TAG"], e["REG"]
role = lambda n: f"arn:aws:iam::{ACC}:role/{P}-{n}"
HC = {"command": ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)\""],
      "interval": 15, "timeout": 5, "retries": 3, "startPeriod": 30}
env = lambda d: [{"name": k, "value": str(v)} for k, v in d.items()]
DB = {"DB_HOST": e["DB_HOST"], "DB_PORT": 5432, "DB_NAME": "setuppronto", "DB_SSLMODE": "require", "LOG_LEVEL": "INFO"}
logs = lambda s: {"logDriver": "awslogs", "options": {"awslogs-group": f"/ecs/{P}/{s}", "awslogs-region": R, "awslogs-stream-prefix": s}}
tags = [{"key": "Project", "value": P}]

def svc(name, cpu, res, mem, extra, secret, task_cpu=None, task_mem=None):
    td = {"family": f"{P}-{name}", "networkMode": "bridge", "requiresCompatibilities": ["EC2"], "tags": tags,
          "executionRoleArn": role("task-execution-role"), "taskRoleArn": role(f"{name}-task-role"),
          "containerDefinitions": [{
              "name": name, "image": f"{REG}/{P}/{name}:{TAG}", "essential": True,
              "cpu": cpu, "memoryReservation": res, "memory": mem,
              "portMappings": [{"name": name, "containerPort": 8080, "hostPort": 0, "protocol": "tcp", "appProtocol": "http"}],
              "environment": env({**DB, **extra}),
              "secrets": [{"name": "DB_PASSWORD", "valueFrom": secret}],
              "healthCheck": HC, "stopTimeout": 30, "logConfiguration": logs(name)}]}
    if task_cpu:  # espaço para o proxy do Service Connect
        td["cpu"], td["memory"] = str(task_cpu), str(task_mem)
    return td

tds = {
  "chat-api": svc("chat-api", 256, 256, 512, {
      "DB_USER": "chat_api", "COMMERCE_API_URL": f"http://commerce-api.{P}.local:8080", "COMMERCE_TIMEOUT_SECONDS": 5,
      "LLM_PROVIDER": "bedrock", "LLM_MODEL_ID": "us.anthropic.claude-haiku-5-5", "AWS_REGION": R},
      e["SEC_CHAT_API"], 512, 768),
  "commerce-api": svc("commerce-api", 256, 256, 512, {
      "DB_USER": "commerce_api", "SQS_QUEUE_URL": e["Q_URL"], "AWS_REGION": R, "OUTBOX_PUBLISHER_ENABLED": "true"},
      e["SEC_COMMERCE_API"], 512, 768),
  "order-worker": svc("order-worker", 256, 192, 384, {
      "DB_USER": "order_worker", "DB_STATEMENT_TIMEOUT_MS": 10000, "SQS_QUEUE_URL": e["Q_URL"], "AWS_REGION": R},
      e["SEC_ORDER_WORKER"]),
  "migrate": {"family": f"{P}-migrate", "networkMode": "bridge", "requiresCompatibilities": ["EC2"], "tags": tags,
      "executionRoleArn": role("task-execution-role"),
      "containerDefinitions": [{
          "name": "migrate", "image": f"{REG}/{P}/db-migrate:{TAG}", "essential": True,
          "cpu": 256, "memory": 256,
          "environment": env({**DB, "DB_USER": "pgadmin", "SEED_ON_MIGRATE": "true"}),
          "secrets": [{"name": "DB_PASSWORD", "valueFrom": e["RDS_SECRET"] + ":password::"},
                      {"name": "CHAT_DB_PASSWORD", "valueFrom": e["SEC_CHAT_API"]},
                      {"name": "COMMERCE_DB_PASSWORD", "valueFrom": e["SEC_COMMERCE_API"]},
                      {"name": "WORKER_DB_PASSWORD", "valueFrom": e["SEC_ORDER_WORKER"]}],
          "logConfiguration": logs("migrate")}]},
}
for n, td in tds.items():
    json.dump(td, open(os.path.expanduser(f"~/td/{n}.json"), "w"), indent=1)
print("json ok:", ", ".join(tds))
PY
for n in migrate commerce-api order-worker chat-api; do
  aws ecs register-task-definition --cli-input-json file://$HOME/td/$n.json --query 'taskDefinition.[family,revision]' --output text
done

echo "== rodando migração (imagem $TAG)"
OUT=$(aws ecs run-task --cluster $C --task-definition $P-migrate --capacity-provider-strategy capacityProvider=$P-cp,weight=1 --started-by migrate --output json)
TASK=$(echo "$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["tasks"][0]["taskArn"] if d["tasks"] else ""); [print("FALHA:", f, file=sys.stderr) for f in d["failures"]]')
[ -n "$TASK" ] || { echo "run-task falhou"; exit 1; }
aws ecs wait tasks-stopped --cluster $C --tasks $TASK
aws ecs describe-tasks --cluster $C --tasks $TASK --query 'tasks[0].[containers[0].exitCode,stoppedReason,containers[0].reason]' --output text
aws logs tail /ecs/$P/migrate --since 15m --format short | tail -20
echo "BLOCO 4 FIM"
