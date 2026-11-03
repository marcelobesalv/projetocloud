data "aws_iam_policy_document" "assume_ec2" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "assume_tasks" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.conta]
    }
  }
}

# IAM é global na conta: o nome leva a região para não colidir com a montagem de Ohio.
locals {
  iam = "${local.p}-${var.region}"
}

# --- instâncias do cluster ---
resource "aws_iam_role" "instance" {
  name               = "${local.iam}-ecs-instance-role"
  assume_role_policy = data.aws_iam_policy_document.assume_ec2.json
}

resource "aws_iam_role_policy_attachment" "instance" {
  for_each = toset([
    "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role",
    "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
  ])
  role       = aws_iam_role.instance.name
  policy_arn = each.value
}

resource "aws_iam_instance_profile" "instance" {
  name = "${local.iam}-ecs-instance-role"
  role = aws_iam_role.instance.name
}

# --- execution role: puxa imagem, escreve log, lê só os 4 segredos ---
resource "aws_iam_role" "execution" {
  name               = "${local.iam}-task-execution-role"
  assume_role_policy = data.aws_iam_policy_document.assume_tasks.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  name = "ler-segredos"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "secretsmanager:GetSecretValue"
      Resource = concat([for s in aws_secretsmanager_secret.db : s.arn], [aws_db_instance.db.master_user_secret[0].secret_arn])
    }]
  })
}

# --- chat-api: só o modelo configurado no Bedrock ---
locals {
  modelo        = join(".", slice(split(".", var.llm_model_id), 1, length(split(".", var.llm_model_id))))
  perfil_arn    = "arn:aws:bedrock:${var.region}:${local.conta}:inference-profile/${var.llm_model_id}"
  perfil_global = startswith(var.llm_model_id, "global.")
  acoes_bedrock = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]

  # Perfil global: as 3 declarações exigidas pela documentação de global cross-Region inference.
  bedrock_global = [
    {
      Sid       = "PerfilGlobal", Effect = "Allow", Action = local.acoes_bedrock, Resource = local.perfil_arn
      Condition = { StringEquals = { "aws:RequestedRegion" = var.region } }
    },
    {
      Sid       = "ModeloNaRegiao", Effect = "Allow", Action = local.acoes_bedrock
      Resource  = "arn:aws:bedrock:${var.region}::foundation-model/${local.modelo}"
      Condition = { StringEquals = { "aws:RequestedRegion" = var.region, "bedrock:InferenceProfileArn" = local.perfil_arn } }
    },
    {
      Sid       = "ModeloGlobal", Effect = "Allow", Action = local.acoes_bedrock
      Resource  = "arn:aws:bedrock:::foundation-model/${local.modelo}"
      Condition = { StringEquals = { "aws:RequestedRegion" = "unspecified", "bedrock:InferenceProfileArn" = local.perfil_arn } }
    },
  ]
  # Perfil geográfico (us., eu., ...): o perfil e o modelo em qualquer região do perfil.
  bedrock_geo = [
    { Sid = "Perfil", Effect = "Allow", Action = local.acoes_bedrock, Resource = local.perfil_arn },
    {
      Sid       = "ModeloViaPerfil", Effect = "Allow", Action = local.acoes_bedrock
      Resource  = "arn:aws:bedrock:*::foundation-model/${local.modelo}"
      Condition = { StringEquals = { "bedrock:InferenceProfileArn" = local.perfil_arn } }
    },
  ]
}

resource "aws_iam_role" "task" {
  for_each           = toset(["chat-api", "commerce-api", "order-worker"])
  name               = "${local.iam}-${each.key}-task-role"
  assume_role_policy = data.aws_iam_policy_document.assume_tasks.json
}

resource "aws_iam_role_policy" "chat" {
  name = "bedrock-haiku"
  role = aws_iam_role.task["chat-api"].id
  # (jsonencode em cada lado: as duas listas têm formatos diferentes)
  policy = local.perfil_global ? jsonencode({ Version = "2012-10-17", Statement = local.bedrock_global }) : jsonencode({ Version = "2012-10-17", Statement = local.bedrock_geo })
}

resource "aws_iam_role_policy" "commerce" {
  name = "enviar-pedidos"
  role = aws_iam_role.task["commerce-api"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sqs:SendMessage", "sqs:GetQueueUrl", "sqs:GetQueueAttributes"]
      Resource = aws_sqs_queue.orders.arn
    }]
  })
}

resource "aws_iam_role_policy" "worker" {
  name = "consumir-pedidos"
  role = aws_iam_role.task["order-worker"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueUrl", "sqs:GetQueueAttributes"]
      Resource = aws_sqs_queue.orders.arn
    }]
  })
}
