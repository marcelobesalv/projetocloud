output "url" {
  description = "Endereço público do chat"
  value       = "http://${aws_lb.main.dns_name}/"
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

output "proximo_passo" {
  value = var.deploy_app ? "Aplicação implantada (imagem ${var.image_tag})." : "Publique as imagens (./push.sh ${var.region} ${local.conta}) e rode: terraform apply -var deploy_app=true -var image_tag=<tag>"
}
