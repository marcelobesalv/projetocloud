# Imagens construídas pelo próprio apply, dentro da AWS: o código dos serviços vai para um bucket S3 da região e um
# projeto do CodeBuild (Docker em modo privilegiado) faz o build e o push no ECR da região. Assim um único
# `terraform apply` cria o ambiente inteiro, sem Docker na máquina de quem roda e sem depender de imagens de outra região.
#
# A tag é um hash do conteúdo do código: mudou o código → tag nova → build novo, nova revisão das task definitions,
# migração e deploy. Código igual → mesma tag → nada a fazer (as tags no ECR são imutáveis e o build pula as que já
# existem). Para usar imagens publicadas de outro jeito (push.sh), passe -var image_tag=<tag> e o build não roda.

locals {
  imagens = {
    chat-api     = { dir = "${path.module}/../../services/chat-api", alvo = "runtime" }
    commerce-api = { dir = "${path.module}/../../services/commerce-api", alvo = "runtime" }
    order-worker = { dir = "${path.module}/../../services/order-worker", alvo = "runtime" }
    db-migrate   = { dir = "${path.module}/../../db", alvo = "" }
  }
  construir_imagens = var.image_tag == ""
  image_tag = local.construir_imagens ? substr(sha1(join(",", [
    for k in sort(keys(local.imagens)) : data.archive_file.fonte[k].output_sha256
  ])), 0, 12) : var.image_tag
}

data "archive_file" "fonte" {
  for_each    = local.imagens
  type        = "zip"
  source_dir  = each.value.dir
  output_path = "${path.module}/.build/${each.key}.zip"
  excludes    = ["**/__pycache__/**", "**/.pytest_cache/**", "**/*.pyc", ".venv/**"]
}

# --- bucket com o código a construir: privado, criptografado, só TLS, expira em 30 dias
resource "aws_s3_bucket" "fontes" {
  bucket        = "${local.p}-build-${local.conta}-${var.region}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "fontes" {
  bucket                  = aws_s3_bucket.fontes.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "fontes" {
  bucket = aws_s3_bucket.fontes.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "fontes" {
  bucket = aws_s3_bucket.fontes.id
  rule {
    id     = "expirar"
    status = "Enabled"
    filter {}
    expiration {
      days = 30
    }
  }
}

resource "aws_s3_bucket_policy" "fontes" {
  bucket = aws_s3_bucket.fontes.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "SoTLS"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.fontes.arn, "${aws_s3_bucket.fontes.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.fontes]
}

resource "aws_s3_object" "fonte" {
  for_each = local.construir_imagens ? local.imagens : {}
  bucket   = aws_s3_bucket.fontes.id
  key      = "fontes/${local.image_tag}/${each.key}.zip"
  source   = data.archive_file.fonte[each.key].output_path
  etag     = data.archive_file.fonte[each.key].output_md5
}

# --- CodeBuild: role de menor privilégio (ler o bucket, publicar nos 4 repositórios, escrever o próprio log)
resource "aws_cloudwatch_log_group" "build" {
  name              = "/codebuild/${local.p}-imagens"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "build" {
  name = "${local.iam}-codebuild-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "codebuild.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = local.conta } }
    }]
  })
}

resource "aws_iam_role_policy" "build" {
  name = "construir-imagens"
  role = aws_iam_role.build.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.fontes.arn}/fontes/*" },
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      {
        Effect = "Allow"
        Action = ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage", "ecr:DescribeImages"]
        Resource = [for r in aws_ecr_repository.repo : r.arn]
      },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.build.arn}:*" },
    ]
  })
}

resource "aws_codebuild_project" "imagens" {
  name          = "${local.p}-imagens"
  description   = "Constrói e publica no ECR as imagens dos serviços (disparado pelo terraform apply)"
  service_role  = aws_iam_role.build.arn
  build_timeout = 30

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    type            = "LINUX_CONTAINER"
    compute_type    = "BUILD_GENERAL1_MEDIUM"
    image           = "aws/codebuild/amazonlinux2-x86_64-standard:5.0"
    privileged_mode = true # docker build dentro do CodeBuild
    environment_variable {
      name  = "REGISTRY"
      value = local.registry
    }
    environment_variable {
      name  = "BUCKET"
      value = aws_s3_bucket.fontes.id
    }
    environment_variable {
      name  = "PREFIXO"
      value = local.p
    }
    environment_variable {
      name  = "IMAGENS" # nome:alvo (alvo vazio = estágio final do Dockerfile)
      value = join(" ", [for k, v in local.imagens : "${k}:${v.alvo}"])
    }
    environment_variable {
      name  = "TAG" # sobrescrito a cada build por scripts/build-imagens.sh
      value = "definida-no-disparo"
    }
  }

  logs_config {
    cloudwatch_logs {
      group_name = aws_cloudwatch_log_group.build.name
    }
  }

  source {
    type      = "NO_SOURCE"
    buildspec = <<-YAML
      version: 0.2
      env:
        shell: bash
      phases:
        pre_build:
          commands:
            - aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY"
        build:
          commands:
            - |
              set -euo pipefail
              for item in $IMAGENS; do
                nome="$${item%%:*}"; alvo="$${item#*:}"; repo="$PREFIXO/$nome"; imagem="$REGISTRY/$repo:$TAG"
                if aws ecr describe-images --repository-name "$repo" --image-ids imageTag="$TAG" >/dev/null 2>&1; then
                  echo ">> $imagem já existe no ECR; pulando"; continue
                fi
                echo ">> build $imagem"
                rm -rf "/tmp/$nome" && mkdir -p "/tmp/$nome"
                aws s3 cp "s3://$BUCKET/fontes/$TAG/$nome.zip" "/tmp/$nome.zip" --only-show-errors
                unzip -q "/tmp/$nome.zip" -d "/tmp/$nome"
                docker build $${alvo:+--target "$alvo"} -t "$imagem" "/tmp/$nome"
                docker push "$imagem"
              done
    YAML
  }

  depends_on = [aws_iam_role_policy.build]
}

# Dispara o build e espera; o apply falha se o build falhar. Roda de novo quando a tag (o código) muda.
resource "terraform_data" "imagens" {
  count            = local.construir_imagens ? 1 : 0
  triggers_replace = [local.image_tag]
  provisioner "local-exec" {
    command = "bash ${path.module}/scripts/build-imagens.sh ${var.region} ${aws_codebuild_project.imagens.name} ${local.image_tag}"
  }
  depends_on = [aws_s3_object.fonte, aws_codebuild_project.imagens, aws_ecr_repository.repo]
}
