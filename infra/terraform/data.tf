# Fila de pedidos + DLQ, repositórios ECR, log groups, segredos e RDS.

resource "aws_sqs_queue" "dlq" {
  name                      = "${local.p}-orders-dlq"
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "orders" {
  name                       = "${local.p}-orders"
  visibility_timeout_seconds = 60
  receive_wait_time_seconds  = 20
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 5
  })
}

resource "aws_ecr_repository" "repo" {
  for_each             = toset(["chat-api", "commerce-api", "order-worker", "db-migrate"])
  name                 = "${local.p}/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true
  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "repo" {
  for_each   = aws_ecr_repository.repo
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Guarda as 10 imagens mais recentes"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
      action       = { type = "expire" }
    }]
  })
}

resource "aws_cloudwatch_log_group" "svc" {
  for_each          = toset(["chat-api", "commerce-api", "order-worker", "migrate", "service-connect"])
  name              = "/ecs/${local.p}/${each.key}"
  retention_in_days = var.log_retention_days
}

# Senha de cada usuário de serviço (o job de migração cria os usuários com elas).
resource "random_password" "db" {
  for_each = toset(["chat_api", "commerce_api", "order_worker"])
  length   = 40
  special  = false
}

resource "aws_secretsmanager_secret" "db" {
  for_each                = random_password.db
  name                    = "${local.p}/db/${each.key}"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "db" {
  for_each      = random_password.db
  secret_id     = aws_secretsmanager_secret.db[each.key].id
  secret_string = each.value.result
}

resource "aws_db_subnet_group" "db" {
  name       = "${local.p}-db-subnets"
  subnet_ids = aws_subnet.data[*].id
}

resource "aws_db_instance" "db" {
  identifier                  = "${local.p}-db"
  engine                      = "postgres"
  engine_version              = "16"
  instance_class              = var.db_instance_class
  allocated_storage           = 20
  storage_type                = "gp3"
  storage_encrypted           = true
  db_name                     = var.db_name
  username                    = var.db_master_user
  manage_master_user_password = true
  db_subnet_group_name        = aws_db_subnet_group.db.name
  vpc_security_group_ids      = [aws_security_group.rds.id]
  publicly_accessible         = false
  multi_az                    = false
  backup_retention_period     = 1
  skip_final_snapshot         = true
  deletion_protection         = false
  apply_immediately           = true
}
