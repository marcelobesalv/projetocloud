output "url" {
  description = "Endereço público do chat (ALB, HTTP)"
  value       = "http://${aws_lb.main.dns_name}/"
}

output "alb_dns" {
  value = aws_lb.main.dns_name
}

output "ecr_registry" {
  value = local.registry
}

output "ecr_repos" {
  value = { for k, r in aws_ecr_repository.repo : k => r.repository_url }
}

output "cluster" {
  value = aws_ecs_cluster.main.name
}

output "fila_pedidos" {
  value = aws_sqs_queue.orders.url
}

output "dlq" {
  value = aws_sqs_queue.dlq.url
}

output "rds_endpoint" {
  value = aws_db_instance.db.address
}

output "image_tag" {
  description = "Tag das imagens em uso (hash do código quando construídas pelo CodeBuild)"
  value       = local.image_tag
}

output "proximo_passo" {
  value = var.deploy_app ? "Aplicação implantada (imagem ${local.image_tag}): abra o endereço em url." : "Só a infraestrutura foi criada; rode terraform apply sem -var deploy_app=false para subir a aplicação."
}
