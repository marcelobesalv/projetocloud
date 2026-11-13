variable "region" {
  description = "Região da AWS"
  type        = string
  default     = "sa-east-1"
}

variable "project" {
  description = "Prefixo dos nomes e valor da tag Project"
  type        = string
  default     = "setup-pronto"
}

variable "vpc_cidr" {
  type    = string
  default = "10.30.0.0/16"
}

variable "instance_type" {
  description = "Instâncias do cluster ECS"
  type        = string
  default     = "t3.medium"
}

variable "asg_min" {
  type    = number
  default = 2
}

variable "asg_max" {
  type    = number
  default = 4
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_name" {
  type    = string
  default = "setuppronto"
}

variable "db_master_user" {
  type    = string
  default = "pgadmin"
}

variable "db_password_version" {
  description = "Incremente para gerar e gravar novas senhas dos usuários de serviço"
  type        = number
  default     = 1
}

variable "deploy_app" {
  description = "Cria task definitions, roda a migração e sobe os serviços. false = só a infraestrutura (rede, dados, ECR...)."
  type        = bool
  default     = true
}

variable "image_tag" {
  description = "Vazio (padrão): as imagens são construídas pelo CodeBuild com a tag = hash do código. Preencha só para usar imagens já publicadas (ex.: push.sh)."
  type        = string
  default     = ""

  validation {
    condition     = var.image_tag == "" || can(regex("^[0-9A-Za-z._-]+$", var.image_tag))
    error_message = "image_tag inválida."
  }
}

variable "llm_model_id" {
  description = "Inference profile do Bedrock (sa-east-1 só tem o global.*)"
  type        = string
  default     = "global.anthropic.claude-haiku-5-5"
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "alarm_email" {
  description = "E-mail para receber os alarmes (vazio = sem assinatura; a AWS manda um e-mail de confirmação)"
  type        = string
  default     = ""
}

variable "services" {
  description = "Tamanho e escala de cada serviço"
  type = map(object({
    cpu         = number
    mem_reserva = number
    mem_limite  = number
    task_cpu    = optional(number)
    task_mem    = optional(number)
    desired     = number
    min         = number
    max         = number
  }))
  default = {
    chat-api     = { cpu = 256, mem_reserva = 256, mem_limite = 512, task_cpu = 512, task_mem = 768, desired = 2, min = 2, max = 6 }
    commerce-api = { cpu = 256, mem_reserva = 256, mem_limite = 512, task_cpu = 512, task_mem = 768, desired = 2, min = 2, max = 6 }
    order-worker = { cpu = 256, mem_reserva = 192, mem_limite = 384, desired = 2, min = 1, max = 4 }
  }
}
