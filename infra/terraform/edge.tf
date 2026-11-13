# Borda: CloudFront (HTTPS com o certificado *.cloudfront.net, sem domínio próprio) + WAF, e o ALB só aceitando
# o CloudFront: o security group libera só a lista de IPs de origem do CloudFront e o listener só encaminha
# requisições com o cabeçalho secreto que o CloudFront acrescenta. Assim ninguém contorna o WAF chamando o ALB direto.
#
# O WAF de uma distribuição CloudFront é global e tem de ser criado em us-east-1 (provider aws.us_east_1), assim
# como o log group dos logs dele. O resto continua na região do ambiente.

variable "waf_limite_chat" {
  description = "Máximo de POST /chat por IP em 5 min (cada turno chama o LLM); acima disso o WAF responde 429"
  type        = number
  default     = 100
}

variable "waf_limite_geral" {
  description = "Máximo de requisições por IP em 5 min, qualquer caminho"
  type        = number
  default     = 3000
}

# Valor do cabeçalho de origem: gerado aqui, nunca escrito em arquivo. Fica no state (criptografado com KMS e de
# acesso restrito) porque a configuração da distribuição precisa dele; trocar: terraform apply -replace=random_password.origem
resource "random_password" "origem" {
  length  = 40
  special = false
}

locals {
  cabecalho_origem = "X-Origem-CloudFront"
}

# --- ALB: só o CloudFront entra
data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_vpc_security_group_ingress_rule" "alb_cloudfront" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP so dos servidores de origem do CloudFront"
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront.id
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_lb_listener_rule" "via_cloudfront" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 10
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.chat.arn
  }
  condition {
    http_header {
      http_header_name = local.cabecalho_origem
      values           = [random_password.origem.result]
    }
  }
}

# --- WAF (us-east-1, escopo CloudFront)
resource "aws_wafv2_web_acl" "borda" {
  provider    = aws.us_east_1
  name        = "${local.p}-${var.region}-borda"
  description = "Limite por IP e regras gerenciadas na frente do chat - ${var.region}"
  scope       = "CLOUDFRONT"

  default_action {
    allow {}
  }

  custom_response_body {
    key          = "limite"
    content_type = "APPLICATION_JSON"
    content      = jsonencode({ erro = "Muitas requisições em pouco tempo. Tente de novo em alguns minutos.", codigo = "limite_requisicoes" })
  }

  rule {
    name     = "limite-chat"
    priority = 0
    action {
      block {
        custom_response {
          response_code            = 429
          custom_response_body_key = "limite"
        }
      }
    }
    statement {
      rate_based_statement {
        limit                 = var.waf_limite_chat
        aggregate_key_type    = "IP"
        evaluation_window_sec = 300
        scope_down_statement {
          and_statement {
            statement {
              byte_match_statement {
                search_string         = "/chat"
                positional_constraint = "EXACTLY"
                field_to_match {
                  uri_path {}
                }
                text_transformation {
                  priority = 0
                  type     = "NONE"
                }
              }
            }
            statement {
              byte_match_statement {
                search_string         = "POST"
                positional_constraint = "EXACTLY"
                field_to_match {
                  method {}
                }
                text_transformation {
                  priority = 0
                  type     = "NONE"
                }
              }
            }
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      sampled_requests_enabled   = true
      metric_name                = "limite-chat"
    }
  }

  rule {
    name     = "limite-geral"
    priority = 1
    action {
      block {
        custom_response {
          response_code            = 429
          custom_response_body_key = "limite"
        }
      }
    }
    statement {
      rate_based_statement {
        limit                 = var.waf_limite_geral
        aggregate_key_type    = "IP"
        evaluation_window_sec = 300
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      sampled_requests_enabled   = true
      metric_name                = "limite-geral"
    }
  }

  # Regras gerenciadas da AWS: IPs com má reputação, entradas maliciosas conhecidas e o conjunto comum (OWASP).
  dynamic "rule" {
    for_each = ["AWSManagedRulesAmazonIpReputationList", "AWSManagedRulesKnownBadInputsRuleSet", "AWSManagedRulesCommonRuleSet"]
    content {
      name     = rule.value
      priority = 2 + rule.key
      override_action {
        none {}
      }
      statement {
        managed_rule_group_statement {
          vendor_name = "AWS"
          name        = rule.value
        }
      }
      visibility_config {
        cloudwatch_metrics_enabled = true
        sampled_requests_enabled   = true
        metric_name                = rule.value
      }
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    sampled_requests_enabled   = true
    metric_name                = "${local.p}-borda"
  }
}

# Logs só das requisições bloqueadas (o nome do log group tem de começar com aws-waf-logs-).
resource "aws_cloudwatch_log_group" "waf" {
  provider          = aws.us_east_1
  name              = "aws-waf-logs-${local.p}-${var.region}"
  retention_in_days = var.log_retention_days
}

resource "aws_wafv2_web_acl_logging_configuration" "borda" {
  provider                = aws.us_east_1
  resource_arn            = aws_wafv2_web_acl.borda.arn
  log_destination_configs = [aws_cloudwatch_log_group.waf.arn]
  logging_filter {
    default_behavior = "DROP"
    filter {
      behavior    = "KEEP"
      requirement = "MEETS_ANY"
      condition {
        action_condition {
          action = "BLOCK"
        }
      }
    }
  }
}

resource "aws_cloudwatch_query_definition" "waf_bloqueios" {
  provider        = aws.us_east_1
  name            = "${local.p}/waf-bloqueios-${var.region}"
  log_group_names = [aws_cloudwatch_log_group.waf.name]
  query_string    = <<-Q
    fields @timestamp, httpRequest.clientIp, httpRequest.httpMethod, httpRequest.uri, terminatingRuleId
    | stats count(*) as bloqueios by terminatingRuleId, httpRequest.clientIp
    | sort bloqueios desc
  Q
}

# --- CloudFront
data "aws_cloudfront_cache_policy" "sem_cache" {
  name = "Managed-CachingDisabled" # o chat é dinâmico; nada é guardado na borda
}

data "aws_cloudfront_origin_request_policy" "tudo_menos_host" {
  name = "Managed-AllViewerExceptHostHeader" # repassa corpo, query string e cabeçalhos do cliente ao ALB
}

resource "aws_cloudfront_distribution" "main" {
  enabled         = true
  comment         = "${local.p}-${var.region}" # os scripts de ops/ acham a distribuição por este comentário
  is_ipv6_enabled = true
  http_version    = "http2and3"
  price_class     = "PriceClass_All" # inclui as bordas da América do Sul
  web_acl_id      = aws_wafv2_web_acl.borda.arn

  origin {
    origin_id   = "alb"
    domain_name = aws_lb.main.dns_name
    custom_origin_config {
      http_port                = 80
      https_port               = 443
      origin_protocol_policy   = "http-only" # sem domínio próprio o ALB não tem certificado; ver docs/runbook.md
      origin_ssl_protocols     = ["TLSv1.2"]
      origin_read_timeout      = 60 # o turno do chat tem orçamento de 50 s (o padrão do CloudFront é 30 s)
      origin_keepalive_timeout = 5
    }
    custom_header {
      name  = local.cabecalho_origem
      value = random_password.origem.result
    }
  }

  default_cache_behavior {
    target_origin_id         = "alb"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.sem_cache.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.tudo_menos_host.id
    compress                 = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}
