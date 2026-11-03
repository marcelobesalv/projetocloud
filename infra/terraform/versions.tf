terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
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
