terraform {
  required_version = ">= 1.11" # write-only (senhas fora do state) e lockfile no S3
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.90"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7" # ephemeral random_password
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7" # código dos serviços para o CodeBuild (build.tf)
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
    }
  }
}

# WAF e logs da borda: recursos do CloudFront são globais e o WAF deles só existe em us-east-1.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "atual" {}
data "aws_availability_zones" "disponiveis" {
  state = "available"
}

locals {
  p       = var.project
  conta   = data.aws_caller_identity.atual.account_id
  azs     = slice(data.aws_availability_zones.disponiveis.names, 0, 2)
  cluster = "${var.project}-cluster"
}
