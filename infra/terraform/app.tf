# Fase 2 (deploy_app = true): task definitions, migração, serviços e auto scaling das tasks.

locals {
  app      = var.deploy_app ? var.services : {}
  registry = "${local.conta}.dkr.ecr.${var.region}.amazonaws.com"
  db_env = {
    DB_HOST    = aws_db_instance.db.address
    DB_PORT    = "5432"
    DB_NAME    = var.db_name
    DB_SSLMODE = "require"
    LOG_LEVEL  = "INFO"
  }
  env_servico = {
    chat-api = {
      DB_USER                  = "chat_api"
      COMMERCE_API_URL         = "http://commerce-api.${local.p}.local:8080"
      COMMERCE_TIMEOUT_SECONDS = "5"
      LLM_PROVIDER             = "bedrock"
      LLM_MODEL_ID             = var.llm_model_id
      AWS_REGION               = var.region
    }
    commerce-api = {
      DB_USER                  = "commerce_api"
      SQS_QUEUE_URL            = aws_sqs_queue.orders.url
      AWS_REGION               = var.region
      OUTBOX_PUBLISHER_ENABLED = "true"
    }
    order-worker = {
      DB_USER                 = "order_worker"
      DB_STATEMENT_TIMEOUT_MS = "10000"
      SQS_QUEUE_URL           = aws_sqs_queue.orders.url
      AWS_REGION              = var.region
    }
  }
  segredo = {
    chat-api     = aws_secretsmanager_secret.db["chat_api"].arn
    commerce-api = aws_secretsmanager_secret.db["commerce_api"].arn
    order-worker = aws_secretsmanager_secret.db["order_worker"].arn
  }
  logs = { for k in ["chat-api", "commerce-api", "order-worker", "migrate", "service-connect"] : k => {
    logDriver = "awslogs"
    options = {
      "awslogs-group"         = aws_cloudwatch_log_group.svc[k].name
      "awslogs-region"        = var.region
      "awslogs-stream-prefix" = k
    }
  } }
  healthcheck = {
    command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)\""]
    interval    = 15
    timeout     = 5
    retries     = 3
    startPeriod = 30
  }
}

resource "aws_ecs_task_definition" "svc" {
  for_each                 = local.app
  family                   = "${local.p}-${each.key}"
  network_mode             = "bridge"
  requires_compatibilities = ["EC2"]
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task[each.key].arn
  # Tamanho de task só onde há proxy do Service Connect.
  cpu    = each.value.task_cpu == null ? null : tostring(each.value.task_cpu)
  memory = each.value.task_mem == null ? null : tostring(each.value.task_mem)

  container_definitions = jsonencode([{
    name              = each.key
    image             = "${aws_ecr_repository.repo[each.key].repository_url}:${var.image_tag}"
    essential         = true
    cpu               = each.value.cpu
    memoryReservation = each.value.mem_reserva
    memory            = each.value.mem_limite
    portMappings      = [{ name = each.key, containerPort = 8080, hostPort = 0, protocol = "tcp", appProtocol = "http" }]
    environment       = [for k, v in merge(local.db_env, local.env_servico[each.key]) : { name = k, value = v }]
    secrets           = [{ name = "DB_PASSWORD", valueFrom = local.segredo[each.key] }]
    healthCheck       = local.healthcheck
    stopTimeout       = 30
    logConfiguration  = local.logs[each.key]
  }])
}

resource "aws_ecs_task_definition" "migrate" {
  count                    = var.deploy_app ? 1 : 0
  family                   = "${local.p}-migrate"
  network_mode             = "bridge"
  requires_compatibilities = ["EC2"]
  execution_role_arn       = aws_iam_role.execution.arn
  container_definitions = jsonencode([{
    name        = "migrate"
    image       = "${aws_ecr_repository.repo["db-migrate"].repository_url}:${var.image_tag}"
    essential   = true
    cpu         = 256
    memory      = 256
    environment = [for k, v in merge(local.db_env, { DB_USER = var.db_master_user, SEED_ON_MIGRATE = "true" }) : { name = k, value = v }]
    secrets = [
      { name = "DB_PASSWORD", valueFrom = "${aws_db_instance.db.master_user_secret[0].secret_arn}:password::" },
      { name = "CHAT_DB_PASSWORD", valueFrom = local.segredo["chat-api"] },
      { name = "COMMERCE_DB_PASSWORD", valueFrom = local.segredo["commerce-api"] },
      { name = "WORKER_DB_PASSWORD", valueFrom = local.segredo["order-worker"] },
    ]
    logConfiguration = local.logs["migrate"]
  }])
  lifecycle {
    precondition {
      condition     = var.image_tag != ""
      error_message = "deploy_app = true exige image_tag (ex.: -var image_tag=37791f6)."
    }
  }
}

# Roda a migração a cada nova revisão da task definition (= nova imagem), antes dos serviços.
resource "terraform_data" "migrate" {
  count            = var.deploy_app ? 1 : 0
  triggers_replace = [aws_ecs_task_definition.migrate[0].arn]
  provisioner "local-exec" {
    command = "${path.module}/scripts/run-migrate.sh ${var.region} ${aws_ecs_cluster.main.name} ${aws_ecs_task_definition.migrate[0].arn} ${aws_ecs_capacity_provider.cp.name}"
  }
  depends_on = [
    aws_ecs_cluster_capacity_providers.main,
    aws_iam_role_policy.execution_secrets,
    aws_secretsmanager_secret_version.db,
    aws_vpc_security_group_ingress_rule.rds_from_ecs,
    aws_route_table_association.app,
  ]
}

# commerce-api e order-worker primeiro; o chat-api só depois (ver abaixo).
resource "aws_ecs_service" "svc" {
  for_each                           = { for k, v in local.app : k => v if k != "chat-api" }
  name                               = each.key
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.svc[each.key].arn
  desired_count                      = each.value.desired
  enable_ecs_managed_tags            = true
  propagate_tags                     = "SERVICE"
  deployment_minimum_healthy_percent = 50
  deployment_maximum_percent         = 200

  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.cp.name
    weight            = 1
  }
  ordered_placement_strategy {
    type  = "spread"
    field = "attribute:ecs.availability-zone"
  }
  ordered_placement_strategy {
    type  = "binpack"
    field = "memory"
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # commerce-api publica "commerce-api.<projeto>.local:8080" no Service Connect.
  dynamic "service_connect_configuration" {
    for_each = each.key == "commerce-api" ? [1] : []
    content {
      enabled   = true
      namespace = aws_service_discovery_http_namespace.sc.arn
      service {
        port_name      = "commerce-api"
        discovery_name = "commerce-api"
        client_alias {
          port     = 8080
          dns_name = "commerce-api.${local.p}.local"
        }
      }
      log_configuration {
        log_driver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.svc["service-connect"].name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = each.key
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [desired_count] # quem ajusta é o auto scaling
  }
  depends_on = [terraform_data.migrate]
}

# O cliente do Service Connect só recebe os aliases que já existem no namespace quando a task sobe:
# se o chat-api subir antes do commerce-api, "commerce-api.<projeto>.local" não resolve até um novo deploy.
resource "aws_ecs_service" "chat" {
  count                              = var.deploy_app ? 1 : 0
  name                               = "chat-api"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.svc["chat-api"].arn
  desired_count                      = var.services["chat-api"].desired
  enable_ecs_managed_tags            = true
  propagate_tags                     = "SERVICE"
  health_check_grace_period_seconds  = 60
  deployment_minimum_healthy_percent = 50
  deployment_maximum_percent         = 200

  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.cp.name
    weight            = 1
  }
  ordered_placement_strategy {
    type  = "spread"
    field = "attribute:ecs.availability-zone"
  }
  ordered_placement_strategy {
    type  = "binpack"
    field = "memory"
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.chat.arn
    container_name   = "chat-api"
    container_port   = 8080
  }
  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.sc.arn
    log_configuration {
      log_driver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.svc["service-connect"].name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "chat-api"
      }
    }
  }

  lifecycle {
    ignore_changes = [desired_count]
  }
  depends_on = [aws_ecs_service.svc, aws_lb_listener.http, aws_iam_role_policy.chat]
}

moved {
  from = aws_ecs_service.svc["chat-api"]
  to   = aws_ecs_service.chat[0]
}

# --- auto scaling das tasks ---
resource "aws_appautoscaling_target" "svc" {
  for_each           = local.app
  service_namespace  = "ecs"
  scalable_dimension = "ecs:service:DesiredCount"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${each.key}"
  min_capacity       = each.value.min
  max_capacity       = each.value.max
  depends_on         = [aws_ecs_service.svc, aws_ecs_service.chat]
}

locals {
  politicas = {
    chat-api = {
      nome   = "chat-requisicoes-por-task"
      alvo   = 30
      metric = { predefined = "ALBRequestCountPerTarget", label = "${aws_lb.main.arn_suffix}/${aws_lb_target_group.chat.arn_suffix}" }
    }
    commerce-api = {
      nome   = "commerce-cpu-60"
      alvo   = 60
      metric = { predefined = "ECSServiceAverageCPUUtilization", label = null }
    }
    order-worker = {
      nome   = "worker-fila-10"
      alvo   = 10
      metric = { predefined = null, label = null }
    }
  }
}

resource "aws_appautoscaling_policy" "svc" {
  for_each           = local.app
  name               = local.politicas[each.key].nome
  policy_type        = "TargetTrackingScaling"
  service_namespace  = "ecs"
  scalable_dimension = "ecs:service:DesiredCount"
  resource_id        = aws_appautoscaling_target.svc[each.key].resource_id

  target_tracking_scaling_policy_configuration {
    target_value       = local.politicas[each.key].alvo
    scale_out_cooldown = 60
    scale_in_cooldown  = 300

    dynamic "predefined_metric_specification" {
      for_each = local.politicas[each.key].metric.predefined == null ? [] : [1]
      content {
        predefined_metric_type = local.politicas[each.key].metric.predefined
        resource_label         = local.politicas[each.key].metric.label
      }
    }
    dynamic "customized_metric_specification" {
      for_each = local.politicas[each.key].metric.predefined == null ? [1] : []
      content {
        namespace   = "AWS/SQS"
        metric_name = "ApproximateNumberOfMessagesVisible"
        statistic   = "Average"
        dimensions {
          name  = "QueueName"
          value = aws_sqs_queue.orders.name
        }
      }
    }
  }
}
