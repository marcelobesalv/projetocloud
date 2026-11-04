# Bloco 6: auto scaling dos servicos, metric filters, alarmes e dashboard
import json, os, subprocess
env = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/sp.env")) if "=" in l)
P, R, C = "setup-pronto", "us-east-2", "setup-pronto-cluster"
NS = "SetupPronto"
ALB = env["ALB_ARN"].split(":loadbalancer/")[1]
TG = env["TG_ARN"].split(":")[-1]
def aws(*a):
    r = subprocess.run(["aws", *a, "--region", R, "--output", "json"], capture_output=True, text=True)
    print(("OK  " if r.returncode == 0 else "ERRO ") + " ".join(a[:2]) + " " + (a[a.index("--policy-name") + 1] if "--policy-name" in a else a[a.index("--alarm-name") + 1] if "--alarm-name" in a else a[a.index("--filter-name") + 1] if "--filter-name" in a else ""), r.stderr.strip()[:300])
    return r.stdout
J = json.dumps

# ---- auto scaling ----
def scal(svc, mn, mx, name, cfg):
    rid = f"service/{C}/{svc}"
    aws("application-autoscaling", "register-scalable-target", "--service-namespace", "ecs", "--scalable-dimension",
        "ecs:service:DesiredCount", "--resource-id", rid, "--min-capacity", str(mn), "--max-capacity", str(mx))
    aws("application-autoscaling", "put-scaling-policy", "--service-namespace", "ecs", "--scalable-dimension",
        "ecs:service:DesiredCount", "--resource-id", rid, "--policy-name", name, "--policy-type", "TargetTrackingScaling",
        "--target-tracking-scaling-policy-configuration", J({**cfg, "ScaleOutCooldown": 60, "ScaleInCooldown": 300}))
scal("chat-api", 2, 6, "chat-requisicoes-por-task", {"TargetValue": 30.0, "PredefinedMetricSpecification": {
    "PredefinedMetricType": "ALBRequestCountPerTarget", "ResourceLabel": f"{ALB}/{TG}"}})
scal("commerce-api", 2, 6, "commerce-cpu-60", {"TargetValue": 60.0, "PredefinedMetricSpecification": {
    "PredefinedMetricType": "ECSServiceAverageCPUUtilization"}})
scal("order-worker", 1, 4, "worker-fila-10", {"TargetValue": 10.0, "CustomizedMetricSpecification": {
    "MetricName": "ApproximateNumberOfMessagesVisible", "Namespace": "AWS/SQS",
    "Dimensions": [{"Name": "QueueName", "Value": f"{P}-orders"}], "Statistic": "Average"}})

# ---- metric filters (logs JSON -> metricas) ----
MF = [
    ("chat-api", "LLMCalls", '{ $.event = "llm_call" }', "1", "Count"),
    ("chat-api", "LLMErrors", '{ $.event = "llm_call" && $.success IS FALSE }', "1", "Count"),
    ("chat-api", "LLMLatency", '{ $.event = "llm_call" && $.success IS TRUE }', "$.latency_ms", "Milliseconds"),
    ("chat-api", "LLMInputTokens", '{ $.event = "llm_call" && $.success IS TRUE }', "$.input_tokens", "Count"),
    ("chat-api", "LLMOutputTokens", '{ $.event = "llm_call" && $.success IS TRUE }', "$.output_tokens", "Count"),
    ("chat-api", "ChatTurns", '{ $.event = "chat_turn" }', "1", "Count"),
    ("chat-api", "CommerceUnavailable", '{ $.event = "commerce_unavailable" }', "1", "Count"),
    ("commerce-api", "OrdersCreated", '{ $.event = "order_created" }', "1", "Count"),
    ("commerce-api", "OutboxPublishFailed", '{ $.event = "outbox_publish_failed" }', "1", "Count"),
    ("order-worker", "OrdersConfirmed", '{ $.event = "order_processed" && $.resultado = "CONFIRMADO" }', "1", "Count"),
    ("order-worker", "OrdersRejected", '{ $.event = "order_processed" && $.resultado = "REJEITADO" }', "1", "Count"),
    ("order-worker", "OrderProcessingTime", '{ $.event = "order_processed" }', "$.duration_ms", "Milliseconds"),
    ("order-worker", "PoisonMessages", '{ $.event = "poison_message" }', "1", "Count"),
]
for grp, name, pat, val, unit in MF:
    t = {"metricName": name, "metricNamespace": NS, "metricValue": val, "unit": unit}
    if val == "1": t["defaultValue"] = 0
    aws("logs", "put-metric-filter", "--log-group-name", f"/ecs/{P}/{grp}", "--filter-name", name,
        "--filter-pattern", pat, "--metric-transformations", J([t]))

# ---- alarmes ----
TOPIC = json.loads(aws("sns", "create-topic", "--name", f"{P}-alertas"))["TopicArn"]
def alarm(name, desc, ns, metric, dims, stat, op, thr, periods=1, period=60, missing="notBreaching"):
    a = ["cloudwatch", "put-metric-alarm", "--alarm-name", f"{P}-{name}", "--alarm-description", desc,
         "--namespace", ns, "--metric-name", metric, "--period", str(period), "--evaluation-periods", str(periods),
         "--threshold", str(thr), "--comparison-operator", op, "--treat-missing-data", missing,
         "--alarm-actions", TOPIC, "--ok-actions", TOPIC]
    a += ["--extended-statistic", stat] if stat.startswith("p") else ["--statistic", stat]
    if dims: a += ["--dimensions", *[f"Name={k},Value={v}" for k, v in dims.items()]]
    aws(*a)
Q = {"QueueName": f"{P}-orders"}; LB = {"LoadBalancer": ALB}; LBTG = {"LoadBalancer": ALB, "TargetGroup": TG}
GT, GE = "GreaterThanThreshold", "GreaterThanOrEqualToThreshold"
alarm("dlq-com-mensagens", "Mensagens na DLQ de pedidos (poison/pedido inexistente)", "AWS/SQS",
      "ApproximateNumberOfMessagesVisible", {"QueueName": f"{P}-orders-dlq"}, "Maximum", GT, 0)
alarm("fila-atrasada", "Mensagem mais antiga da fila de pedidos com mais de 5 min", "AWS/SQS",
      "ApproximateAgeOfOldestMessage", Q, "Maximum", GT, 300, 2)
alarm("alb-5xx", "Mais de 5 respostas 5xx do chat-api em 5 min", "AWS/ApplicationELB",
      "HTTPCode_Target_5XX_Count", LB, "Sum", GT, 5, 1, 300)
alarm("chat-sem-hosts-saudaveis", "Target group do chat-api com task nao saudavel por 3 min", "AWS/ApplicationELB",
      "UnHealthyHostCount", LBTG, "Maximum", GE, 1, 3)
alarm("chat-latencia-p90", "p90 do tempo de resposta do chat acima de 30 s", "AWS/ApplicationELB",
      "TargetResponseTime", LB, "p90", GT, 30, 3)
alarm("llm-erros", "3 ou mais falhas de chamada ao Bedrock em 5 min", NS, "LLMErrors", None, "Sum", GE, 3, 1, 300)
alarm("commerce-indisponivel", "chat-api nao conseguiu falar com o commerce-api", NS, "CommerceUnavailable", None,
      "Sum", GE, 1, 1, 300)
alarm("rds-cpu", "CPU do RDS acima de 80% por 15 min", "AWS/RDS", "CPUUtilization",
      {"DBInstanceIdentifier": f"{P}-db"}, "Average", GT, 80, 3, 300, "missing")
alarm("rds-disco", "Menos de 2 GB livres no RDS", "AWS/RDS", "FreeStorageSpace",
      {"DBInstanceIdentifier": f"{P}-db"}, "Minimum", "LessThanThreshold", 2 * 1024**3, 1, 300, "missing")

# ---- dashboard ----
def w(title, metrics, x, y, stat="Sum", period=60, wd=8, h=6, view="timeSeries", yaxis=None):
    p = {"title": title, "metrics": metrics, "region": R, "stat": stat, "period": period, "view": view}
    if yaxis: p["yAxis"] = yaxis
    return {"type": "metric", "x": x, "y": y, "width": wd, "height": h, "properties": p}
ECS = lambda m, s: ["AWS/ECS", m, "ClusterName", C, "ServiceName", s]
widgets = [
    {"type": "text", "x": 0, "y": 0, "width": 24, "height": 2, "properties": {"markdown":
        "# Setup Pronto (us-east-2)\nchat-api (ALB) -> commerce-api (Service Connect) -> SQS -> order-worker -> RDS. Metricas `SetupPronto/*` vem dos logs JSON (metric filters)."}},
    w("ALB: requisicoes e 5xx", [["AWS/ApplicationELB", "RequestCount", "LoadBalancer", ALB],
        [".", "HTTPCode_Target_5XX_Count", ".", "."], [".", "HTTPCode_ELB_5XX_Count", ".", "."]], 0, 2),
    w("ALB: tempo de resposta (p50/p90)", [["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", ALB, {"stat": "p50"}],
        ["...", {"stat": "p90"}]], 8, 2, "p50"),
    w("Hosts saudaveis do chat", [["AWS/ApplicationELB", "HealthyHostCount", "TargetGroup", TG, "LoadBalancer", ALB],
        [".", "UnHealthyHostCount", ".", ".", ".", "."]], 16, 2, "Maximum"),
    w("CPU por servico (%)", [ECS("CPUUtilization", s) for s in ("chat-api", "commerce-api", "order-worker")], 0, 8, "Average", 60),
    w("Memoria por servico (%)", [ECS("MemoryUtilization", s) for s in ("chat-api", "commerce-api", "order-worker")], 8, 8, "Average", 60),
    w("Fila de pedidos", [["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", f"{P}-orders"],
        [".", "ApproximateNumberOfMessagesNotVisible", ".", "."],
        [".", "ApproximateNumberOfMessagesVisible", ".", f"{P}-orders-dlq", {"label": "DLQ"}]], 16, 8, "Maximum"),
    w("Pedidos", [[NS, "OrdersCreated"], [".", "OrdersConfirmed"], [".", "OrdersRejected"], [".", "PoisonMessages"]], 0, 14, "Sum", 300),
    w("LLM: chamadas, erros, turnos", [[NS, "LLMCalls"], [".", "LLMErrors"], [".", "ChatTurns"], [".", "CommerceUnavailable"]], 8, 14, "Sum", 300),
    w("LLM: latencia (ms)", [[NS, "LLMLatency", {"stat": "p50"}], ["...", {"stat": "p90"}], [NS, "OrderProcessingTime", {"stat": "p90", "label": "worker p90"}]], 16, 14, "p50", 300),
    w("LLM: tokens", [[NS, "LLMInputTokens"], [".", "LLMOutputTokens"]], 0, 20, "Sum", 300),
    w("RDS", [["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", f"{P}-db"], [".", "DatabaseConnections", ".", "."]], 8, 20, "Average", 60),
    {"type": "alarm", "x": 16, "y": 20, "width": 8, "height": 6, "properties": {"title": "Alarmes", "alarms":
        [f"arn:aws:cloudwatch:{R}:202757356229:alarm:{P}-{n}" for n in ("dlq-com-mensagens", "fila-atrasada", "alb-5xx",
         "chat-sem-hosts-saudaveis", "chat-latencia-p90", "llm-erros", "commerce-indisponivel", "rds-cpu", "rds-disco")]}},
]
aws("cloudwatch", "put-dashboard", "--dashboard-name", P, "--dashboard-body", J({"widgets": widgets}))
print("SNS:", TOPIC)
print("BLOCO 6 FIM")
