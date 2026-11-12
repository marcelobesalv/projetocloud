# Auditoria: CloudTrail (S3 + CloudWatch Logs), alarmes de segurança e consultas salvas do Logs Insights.
# O que fazer quando cada alarme dispara: docs/runbook.md.

variable "cloudtrail_multi_region" {
  description = "Trilha de todas as regiões (pega também a etapa manual de Ohio e os eventos globais do IAM, que só chegam a trilhas de us-east-1 ou multirregião). Crie em uma só região por conta."
  type        = bool
  default     = true
}

variable "cloudtrail_retencao_dias" {
  description = "Dias de retenção dos arquivos da trilha no S3"
  type        = number
  default     = 90
}

locals {
  trilha     = "${local.p}-trail"
  trilha_arn = "arn:aws:cloudtrail:${var.region}:${local.conta}:trail/${local.trilha}" # montado para evitar ciclo com a bucket policy
}

# --- bucket da trilha: privado, criptografado, só TLS, só o CloudTrail desta trilha escreve
resource "aws_s3_bucket" "trilha" {
  bucket        = "${local.p}-cloudtrail-${local.conta}-${var.region}"
  force_destroy = true # destroy reproduzível; os arquivos também estão no CloudWatch Logs durante a retenção de lá
}

resource "aws_s3_bucket_ownership_controls" "trilha" {
  bucket = aws_s3_bucket.trilha.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "trilha" {
  bucket                  = aws_s3_bucket.trilha.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "trilha" {
  bucket = aws_s3_bucket.trilha.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "trilha" {
  bucket = aws_s3_bucket.trilha.id
  versioning_configuration {
    status = "Enabled" # um arquivo apagado ou sobrescrito continua recuperável (além da validação de integridade)
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "trilha" {
  bucket = aws_s3_bucket.trilha.id
  rule {
    id     = "expirar"
    status = "Enabled"
    filter {}
    expiration {
      days = var.cloudtrail_retencao_dias
    }
    noncurrent_version_expiration {
      noncurrent_days = 7
    }
  }
}

data "aws_iam_policy_document" "trilha_bucket" {
  statement {
    sid       = "CloudTrailAclCheck"
    actions   = ["s3:GetBucketAcl"]
    resources = [aws_s3_bucket.trilha.arn]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.trilha_arn]
    }
  }
  statement {
    sid       = "CloudTrailWrite"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.trilha.arn}/AWSLogs/${local.conta}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.trilha_arn]
    }
  }
  statement {
    sid       = "SoTLS"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.trilha.arn, "${aws_s3_bucket.trilha.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "trilha" {
  bucket     = aws_s3_bucket.trilha.id
  policy     = data.aws_iam_policy_document.trilha_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.trilha]
}

# --- cópia no CloudWatch Logs: permite metric filters, alarmes e Logs Insights sobre a trilha
resource "aws_cloudwatch_log_group" "trilha" {
  name              = "/${local.p}/cloudtrail"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "trilha" {
  name = "${local.iam}-cloudtrail-logs-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "cloudtrail.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceArn" = local.trilha_arn } }
    }]
  })
}

resource "aws_iam_role_policy" "trilha" {
  name = "escrever-log-group"
  role = aws_iam_role.trilha.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.trilha.arn}:log-stream:*"
    }]
  })
}

resource "aws_cloudtrail" "main" {
  name                          = local.trilha
  s3_bucket_name                = aws_s3_bucket.trilha.id
  is_multi_region_trail         = var.cloudtrail_multi_region
  include_global_service_events = true
  enable_log_file_validation    = true # digest assinado a cada hora: prova que os arquivos não foram alterados
  cloud_watch_logs_group_arn    = "${aws_cloudwatch_log_group.trilha.arn}:*"
  cloud_watch_logs_role_arn     = aws_iam_role.trilha.arn
  depends_on                    = [aws_s3_bucket_policy.trilha, aws_iam_role_policy.trilha]
}

# --- métricas e alarmes de segurança (padrões do CIS AWS Foundations, seção de monitoramento)
locals {
  filtros_seguranca = {
    AcessoNegado = "{ ($.errorCode = \"*UnauthorizedOperation\") || ($.errorCode = \"AccessDenied*\") }"
    MudancaSecurityGroup = join(" || ", [for e in ["AuthorizeSecurityGroupIngress", "AuthorizeSecurityGroupEgress",
      "RevokeSecurityGroupIngress", "RevokeSecurityGroupEgress", "CreateSecurityGroup", "DeleteSecurityGroup",
    "ModifySecurityGroupRules"] : "($.eventName = ${e})"])
    MudancaIAM = join(" || ", [for e in ["DeleteGroupPolicy", "DeleteRolePolicy", "DeleteUserPolicy", "PutGroupPolicy",
      "PutRolePolicy", "PutUserPolicy", "CreatePolicy", "DeletePolicy", "CreatePolicyVersion", "DeletePolicyVersion",
      "AttachRolePolicy", "DetachRolePolicy", "AttachUserPolicy", "DetachUserPolicy", "AttachGroupPolicy",
    "DetachGroupPolicy", "UpdateAssumeRolePolicy", "CreateRole", "DeleteRole", "CreateAccessKey"] : "($.eventName = ${e})"])
    UsoRoot = "{ $.userIdentity.type = \"Root\" && $.userIdentity.invokedBy NOT EXISTS && $.eventType != \"AwsServiceEvent\" }"
    MudancaCloudTrail = join(" || ", [for e in ["CreateTrail", "UpdateTrail", "DeleteTrail", "StartLogging", "StopLogging",
    "PutEventSelectors"] : "($.eventName = ${e})"])
    LeituraSegredos = "{ ($.eventSource = \"secretsmanager.amazonaws.com\") && ($.eventName = \"GetSecretValue\") }"
  }
  # Os padrões que são só uma lista de eventos ganham as chaves aqui.
  padroes_seguranca = { for k, v in local.filtros_seguranca : k => startswith(v, "{") ? v : "{ ${v} }" }

  alarmes_seguranca = {
    seg-acesso-negado          = { desc = "5 ou mais chamadas negadas (AccessDenied/UnauthorizedOperation) em 5 min: permissao faltando ou tentativa indevida", m = "AcessoNegado", lim = 5 }
    seg-mudanca-security-group = { desc = "Security group criado, alterado ou removido (esperado durante terraform apply)", m = "MudancaSecurityGroup", lim = 1 }
    seg-mudanca-iam            = { desc = "Politica ou role do IAM alterada (esperado durante terraform apply)", m = "MudancaIAM", lim = 1 }
    seg-uso-root               = { desc = "Uso do usuario root da conta", m = "UsoRoot", lim = 1 }
    seg-mudanca-cloudtrail     = { desc = "Trilha do CloudTrail criada, alterada, parada ou removida", m = "MudancaCloudTrail", lim = 1 }
  }
}

resource "aws_cloudwatch_log_metric_filter" "seguranca" {
  for_each       = local.padroes_seguranca
  name           = each.key
  log_group_name = aws_cloudwatch_log_group.trilha.name
  pattern        = each.value
  metric_transformation {
    name          = each.key
    namespace     = "${local.ns}/Seguranca"
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "seguranca" {
  for_each            = local.alarmes_seguranca
  alarm_name          = "${local.p}-${each.key}"
  alarm_description   = "${each.value.desc}. Runbook: docs/runbook.md"
  namespace           = "${local.ns}/Seguranca"
  metric_name         = each.value.m
  statistic           = "Sum"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = each.value.lim
  evaluation_periods  = 1
  period              = 300
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alertas.arn]
  ok_actions          = [aws_sns_topic.alertas.arn]
  depends_on          = [aws_cloudwatch_log_metric_filter.seguranca]
}

# --- consultas salvas do Logs Insights (console: CloudWatch > Logs Insights > Consultas salvas > setup-pronto)
locals {
  lg_app = [for s in ["chat-api", "commerce-api", "order-worker"] : aws_cloudwatch_log_group.svc[s].name]
  consultas = {
    "pedido-por-correlation-id" = {
      grupos = local.lg_app
      query  = <<-Q
        fields @timestamp, service, event, message, pedido_id, resultado, motivo
        | filter correlation_id = "COLE_O_CORRELATION_ID_AQUI"
        | sort @timestamp asc
      Q
    }
    "erros-e-avisos-por-servico" = {
      grupos = local.lg_app
      query  = <<-Q
        filter level in ["ERROR", "WARNING"]
        | stats count(*) as ocorrencias by service, event
        | sort ocorrencias desc
      Q
    }
    "pedidos-reenviados-e-travados" = {
      grupos = [aws_cloudwatch_log_group.svc["commerce-api"].name]
      query  = <<-Q
        fields @timestamp, event, pedido_id, reenvio, travados, correlation_id
        | filter event = "order_requeued" or (event = "reconciliation_cycle" and travados > 0)
        | sort @timestamp desc
      Q
    }
    "mensagens-problematicas-da-fila" = {
      grupos = [aws_cloudwatch_log_group.svc["order-worker"].name]
      query  = <<-Q
        fields @timestamp, event, pedido_id, message_id, receive_count, erro
        | filter event in ["poison_message", "order_not_found", "order_transient_error", "sqs_receive_failed"]
        | sort @timestamp desc
      Q
    }
    "llm-latencia-e-falhas-por-minuto" = {
      grupos = [aws_cloudwatch_log_group.svc["chat-api"].name]
      query  = <<-Q
        filter event = "llm_call"
        | stats count(*) as chamadas, pct(latency_ms, 50) as p50_ms, pct(latency_ms, 90) as p90_ms by bin(1m)
      Q
    }
    "cloudtrail-acesso-negado" = {
      grupos = [aws_cloudwatch_log_group.trilha.name]
      query  = <<-Q
        fields @timestamp, userIdentity.arn, eventSource, eventName, errorCode, errorMessage
        | filter errorCode like /AccessDenied|UnauthorizedOperation/
        | sort @timestamp desc
        | limit 50
      Q
    }
    "cloudtrail-mudancas-recentes" = {
      grupos = [aws_cloudwatch_log_group.trilha.name]
      query  = <<-Q
        fields @timestamp, userIdentity.arn, awsRegion, eventSource, eventName, errorCode
        | filter eventName not like /^(Get|Describe|List|Lookup|Head|Batch(Get|Check)|Decrypt|AssumeRole|GenerateDataKey)/
        | sort @timestamp desc
        | limit 100
      Q
    }
  }
}

resource "aws_cloudwatch_query_definition" "q" {
  for_each        = local.consultas
  name            = "${local.p}/${each.key}"
  log_group_names = each.value.grupos
  query_string    = each.value.query
}
