# Métricas a partir dos logs JSON, alarmes (SNS) e dashboard.

locals {
  ns = "SetupPronto"
  metric_filters = {
    LLMCalls            = { grupo = "chat-api", padrao = "{ $.event = \"llm_call\" }", valor = "1", unidade = "Count" }
    LLMErrors           = { grupo = "chat-api", padrao = "{ $.event = \"llm_call\" && $.success IS FALSE }", valor = "1", unidade = "Count" }
    LLMLatency          = { grupo = "chat-api", padrao = "{ $.event = \"llm_call\" && $.success IS TRUE }", valor = "$.latency_ms", unidade = "Milliseconds" }
    LLMInputTokens      = { grupo = "chat-api", padrao = "{ $.event = \"llm_call\" && $.success IS TRUE }", valor = "$.input_tokens", unidade = "Count" }
    LLMOutputTokens     = { grupo = "chat-api", padrao = "{ $.event = \"llm_call\" && $.success IS TRUE }", valor = "$.output_tokens", unidade = "Count" }
    ChatTurns           = { grupo = "chat-api", padrao = "{ $.event = \"chat_turn\" }", valor = "1", unidade = "Count" }
    CommerceUnavailable = { grupo = "chat-api", padrao = "{ $.event = \"commerce_unavailable\" }", valor = "1", unidade = "Count" }
    OrdersCreated       = { grupo = "commerce-api", padrao = "{ $.event = \"order_created\" }", valor = "1", unidade = "Count" }
    OutboxPublishFailed = { grupo = "commerce-api", padrao = "{ $.event = \"outbox_publish_failed\" }", valor = "1", unidade = "Count" }
    OrdersConfirmed     = { grupo = "order-worker", padrao = "{ $.event = \"order_processed\" && $.resultado = \"CONFIRMADO\" }", valor = "1", unidade = "Count" }
    OrdersRejected      = { grupo = "order-worker", padrao = "{ $.event = \"order_processed\" && $.resultado = \"REJEITADO\" }", valor = "1", unidade = "Count" }
    OrderProcessingTime = { grupo = "order-worker", padrao = "{ $.event = \"order_processed\" }", valor = "$.duration_ms", unidade = "Milliseconds" }
    PoisonMessages      = { grupo = "order-worker", padrao = "{ $.event = \"poison_message\" }", valor = "1", unidade = "Count" }
  }
  lb = { LoadBalancer = aws_lb.main.arn_suffix }
  db = { DBInstanceIdentifier = aws_db_instance.db.identifier }
  asg     = { AutoScalingGroupName = aws_autoscaling_group.ecs.name }
  cluster = { ClusterName = local.cluster }
  alarmes_base = {
    dlq-com-mensagens        = { desc = "Mensagens na DLQ de pedidos", ns = "AWS/SQS", m = "ApproximateNumberOfMessagesVisible", dim = jsonencode({ QueueName = aws_sqs_queue.dlq.name }), stat = "Maximum", op = "GreaterThanThreshold", lim = 0, n = 1, per = 60, falta = "notBreaching" }
    fila-atrasada            = { desc = "Mensagem mais antiga da fila com mais de 5 min", ns = "AWS/SQS", m = "ApproximateAgeOfOldestMessage", dim = jsonencode({ QueueName = aws_sqs_queue.orders.name }), stat = "Maximum", op = "GreaterThanThreshold", lim = 300, n = 2, per = 60, falta = "notBreaching" }
    alb-5xx                  = { desc = "Mais de 5 respostas 5xx do chat em 5 min", ns = "AWS/ApplicationELB", m = "HTTPCode_Target_5XX_Count", dim = jsonencode(local.lb), stat = "Sum", op = "GreaterThanThreshold", lim = 5, n = 1, per = 300, falta = "notBreaching" }
    chat-sem-hosts-saudaveis = { desc = "Task do chat nao saudavel por 3 min", ns = "AWS/ApplicationELB", m = "UnHealthyHostCount", dim = jsonencode(merge(local.lb, { TargetGroup = aws_lb_target_group.chat.arn_suffix })), stat = "Maximum", op = "GreaterThanOrEqualToThreshold", lim = 1, n = 3, per = 60, falta = "notBreaching" }
    chat-latencia-p90        = { desc = "p90 do chat acima de 30 s", ns = "AWS/ApplicationELB", m = "TargetResponseTime", dim = jsonencode(local.lb), stat = "p90", op = "GreaterThanThreshold", lim = 30, n = 3, per = 60, falta = "notBreaching" }
    llm-erros                = { desc = "3 ou mais falhas do Bedrock em 5 min", ns = local.ns, m = "LLMErrors", dim = "{}", stat = "Sum", op = "GreaterThanOrEqualToThreshold", lim = 3, n = 1, per = 300, falta = "notBreaching" }
    commerce-indisponivel    = { desc = "chat-api nao conseguiu falar com o commerce-api", ns = local.ns, m = "CommerceUnavailable", dim = "{}", stat = "Sum", op = "GreaterThanOrEqualToThreshold", lim = 1, n = 1, per = 300, falta = "notBreaching" }
    rds-cpu                  = { desc = "CPU do RDS acima de 80% por 15 min", ns = "AWS/RDS", m = "CPUUtilization", dim = jsonencode(local.db), stat = "Average", op = "GreaterThanThreshold", lim = 80, n = 3, per = 300, falta = "missing" }
    rds-disco                = { desc = "Menos de 2 GB livres no RDS", ns = "AWS/RDS", m = "FreeStorageSpace", dim = jsonencode(local.db), stat = "Minimum", op = "LessThanThreshold", lim = 2147483648, n = 1, per = 300, falta = "missing" }
    # Capacidade do cluster e do Auto Scaling Group
    cluster-cpu-reservada     = { desc = "Mais de 90% da CPU do cluster reservada por 15 min", ns = "AWS/ECS", m = "CPUReservation", dim = jsonencode(local.cluster), stat = "Average", op = "GreaterThanThreshold", lim = 90, n = 3, per = 300, falta = "notBreaching" }
    cluster-memoria-reservada = { desc = "Mais de 90% da memoria do cluster reservada por 15 min", ns = "AWS/ECS", m = "MemoryReservation", dim = jsonencode(local.cluster), stat = "Average", op = "GreaterThanThreshold", lim = 90, n = 3, per = 300, falta = "notBreaching" }
    capacidade-insuficiente   = { desc = "Tasks esperando instancia (CapacityProviderReservation > 100%) por 15 min", ns = "AWS/ECS/ManagedScaling", m = "CapacityProviderReservation", dim = jsonencode(merge(local.cluster, { CapacityProviderName = aws_ecs_capacity_provider.cp.name })), stat = "Average", op = "GreaterThanThreshold", lim = 100, n = 3, per = 300, falta = "notBreaching" }
    asg-abaixo-do-minimo      = { desc = "Instancias em servico abaixo do minimo do ASG por 5 min", ns = "AWS/AutoScaling", m = "GroupInServiceInstances", dim = jsonencode(local.asg), stat = "Minimum", op = "LessThanThreshold", lim = var.asg_min, n = 5, per = 60, falta = "notBreaching" }
    asg-no-maximo             = { desc = "ASG no tamanho maximo por 15 min (sem folga para escalar)", ns = "AWS/AutoScaling", m = "GroupInServiceInstances", dim = jsonencode(local.asg), stat = "Minimum", op = "GreaterThanOrEqualToThreshold", lim = var.asg_max, n = 3, per = 300, falta = "notBreaching" }
  }
  # Saúde das tasks: tasks em execução abaixo do mínimo do auto scaling de cada serviço (Container Insights).
  alarmes_tasks = { for s, v in var.services : "${s}-tasks-abaixo-do-minimo" => {
    desc = "${s} com menos de ${v.min} task(s) em execucao por 3 min", ns = "ECS/ContainerInsights", m = "RunningTaskCount"
    dim  = jsonencode(merge(local.cluster, { ServiceName = s })), stat = "Minimum", op = "LessThanThreshold", lim = v.min
    n    = 3, per = 60, falta = "notBreaching"
  } }
  alarmes = merge(local.alarmes_base, local.alarmes_tasks)
}

resource "aws_cloudwatch_log_metric_filter" "mf" {
  for_each       = local.metric_filters
  name           = each.key
  log_group_name = aws_cloudwatch_log_group.svc[each.value.grupo].name
  pattern        = each.value.padrao
  metric_transformation {
    name          = each.key
    namespace     = local.ns
    value         = each.value.valor
    unit          = each.value.unidade
    default_value = each.value.valor == "1" ? "0" : null
  }
}

resource "aws_sns_topic" "alertas" {
  name = "${local.p}-alertas"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alertas.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

resource "aws_cloudwatch_metric_alarm" "a" {
  for_each            = local.alarmes
  alarm_name          = "${local.p}-${each.key}"
  alarm_description   = each.value.desc
  namespace           = each.value.ns
  metric_name         = each.value.m
  dimensions          = each.value.dim == "{}" ? null : jsondecode(each.value.dim)
  statistic           = startswith(each.value.stat, "p") ? null : each.value.stat
  extended_statistic  = startswith(each.value.stat, "p") ? each.value.stat : null
  comparison_operator = each.value.op
  threshold           = each.value.lim
  evaluation_periods  = each.value.n
  period              = each.value.per
  treat_missing_data  = each.value.falta
  alarm_actions       = [aws_sns_topic.alertas.arn]
  ok_actions          = [aws_sns_topic.alertas.arn]
}

locals {
  ecs_metric = { for m in ["CPUUtilization", "MemoryUtilization"] : m => [
    for s in keys(var.services) : ["AWS/ECS", m, "ClusterName", local.cluster, "ServiceName", s]
  ] }
}

locals {
  # Tupla (tipos heterogêneos) — cada gráfico tem o seu formato de métricas.
  graficos = [
    ["ALB: requisicoes e 5xx", 0, 2, "Sum", 60, [["AWS/ApplicationELB", "RequestCount", "LoadBalancer", aws_lb.main.arn_suffix], [".", "HTTPCode_Target_5XX_Count", ".", "."], [".", "HTTPCode_ELB_5XX_Count", ".", "."]]],
    ["ALB: tempo de resposta (p50/p90)", 8, 2, "p50", 60, [["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", aws_lb.main.arn_suffix, { stat = "p50" }], ["...", { stat = "p90" }]]],
    ["Hosts saudaveis do chat", 16, 2, "Maximum", 60, [["AWS/ApplicationELB", "HealthyHostCount", "TargetGroup", aws_lb_target_group.chat.arn_suffix, "LoadBalancer", aws_lb.main.arn_suffix], [".", "UnHealthyHostCount", ".", ".", ".", "."]]],
    ["CPU por servico (%)", 0, 8, "Average", 60, local.ecs_metric["CPUUtilization"]],
    ["Memoria por servico (%)", 8, 8, "Average", 60, local.ecs_metric["MemoryUtilization"]],
    ["Fila de pedidos", 16, 8, "Maximum", 60, [["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.orders.name], [".", "ApproximateNumberOfMessagesNotVisible", ".", "."], [".", "ApproximateNumberOfMessagesVisible", ".", aws_sqs_queue.dlq.name, { label = "DLQ" }]]],
    ["Pedidos", 0, 14, "Sum", 300, [[local.ns, "OrdersCreated"], [".", "OrdersConfirmed"], [".", "OrdersRejected"], [".", "PoisonMessages"]]],
    ["LLM: chamadas, erros, turnos", 8, 14, "Sum", 300, [[local.ns, "LLMCalls"], [".", "LLMErrors"], [".", "ChatTurns"], [".", "CommerceUnavailable"]]],
    ["LLM: latencia (ms)", 16, 14, "p50", 300, [[local.ns, "LLMLatency", { stat = "p50" }], ["...", { stat = "p90" }], [local.ns, "OrderProcessingTime", { stat = "p90", label = "worker p90" }]]],
    ["LLM: tokens", 0, 20, "Sum", 300, [[local.ns, "LLMInputTokens"], [".", "LLMOutputTokens"]]],
    ["RDS", 8, 20, "Average", 60, [["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", aws_db_instance.db.identifier], [".", "DatabaseConnections", ".", "."]]],
    ["Instancias do ASG", 0, 26, "Maximum", 60, [["AWS/AutoScaling", "GroupInServiceInstances", "AutoScalingGroupName", aws_autoscaling_group.ecs.name], [".", "GroupDesiredCapacity", ".", "."], [".", "GroupPendingInstances", ".", "."], [".", "GroupTerminatingInstances", ".", "."], [".", "GroupMaxSize", ".", "."]]],
    ["Reserva do cluster (%)", 8, 26, "Average", 60, [["AWS/ECS", "CPUReservation", "ClusterName", local.cluster], [".", "MemoryReservation", ".", "."], ["AWS/ECS/ManagedScaling", "CapacityProviderReservation", "CapacityProviderName", aws_ecs_capacity_provider.cp.name, "ClusterName", local.cluster]]],
    ["Tasks em execucao", 16, 26, "Maximum", 60, [for s in keys(var.services) : ["ECS/ContainerInsights", "RunningTaskCount", "ClusterName", local.cluster, "ServiceName", s]]],
  ]
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = local.p
  dashboard_body = jsonencode({
    widgets = concat(
      [{ type = "text", x = 0, y = 0, width = 24, height = 2, properties = { markdown = "# Setup Pronto (${var.region})\nchat-api (ALB) -> commerce-api (Service Connect) -> SQS -> order-worker -> RDS. Metricas `${local.ns}/*` vem dos logs JSON." } }],
      [for g in local.graficos : jsondecode(jsonencode({
        type       = "metric", x = g[1], y = g[2], width = 8, height = 6
        properties = { title = g[0], region = var.region, stat = g[3], period = g[4], view = "timeSeries", metrics = g[5] }
      }))],
      [{ type = "alarm", x = 16, y = 20, width = 8, height = 6, properties = { title = "Alarmes", alarms = [for a in aws_cloudwatch_metric_alarm.a : a.arn] } }],
    )
  })
}
