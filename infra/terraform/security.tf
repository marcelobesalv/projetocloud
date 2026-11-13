resource "aws_security_group" "alb" {
  name        = "${local.p}-alb-sg"
  description = "ALB publico"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "${local.p}-alb-sg" }
}

resource "aws_security_group" "ecs" {
  name        = "${local.p}-ecs-instances-sg"
  description = "Instancias ECS"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "${local.p}-ecs-instances-sg" }
}

resource "aws_security_group" "rds" {
  name        = "${local.p}-rds-sg"
  description = "RDS PostgreSQL"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "${local.p}-rds-sg" }
}

# Entrada do ALB: só o CloudFront (aws_vpc_security_group_ingress_rule.alb_cloudfront, em edge.tf).

# Rede bridge: o ALB fala com as portas dinâmicas das instâncias.
resource "aws_vpc_security_group_ingress_rule" "ecs_from_alb" {
  security_group_id            = aws_security_group.ecs.id
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 32768
  to_port                      = 65535
}

# Service Connect entre instâncias (chat -> commerce).
resource "aws_vpc_security_group_ingress_rule" "ecs_self" {
  security_group_id            = aws_security_group.ecs.id
  referenced_security_group_id = aws_security_group.ecs.id
  ip_protocol                  = "-1"
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_ecs" {
  security_group_id            = aws_security_group.rds.id
  referenced_security_group_id = aws_security_group.ecs.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_vpc_security_group_egress_rule" "todos" {
  for_each          = { alb = aws_security_group.alb.id, ecs = aws_security_group.ecs.id }
  security_group_id = each.value
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}
