resource "aws_lb" "main" {
  name               = "${local.p}-alb"
  load_balancer_type = "application"
  internal           = false
  subnets            = aws_subnet.public[*].id
  security_groups    = [aws_security_group.alb.id]
  idle_timeout       = 60 # o turno do chat tem orçamento de 50 s
}

resource "aws_lb_target_group" "chat" {
  name                 = "${local.p}-chat-tg"
  port                 = 8080
  protocol             = "HTTP"
  target_type          = "instance" # rede bridge, porta dinâmica
  vpc_id               = aws_vpc.main.id
  deregistration_delay = 30
  health_check {
    path                = "/health"
    matcher             = "200"
    interval            = 5 # instância perdida sai do balanceamento em ~10 s (era 45 s com 15 s × 3)
    timeout             = 4
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }
}

# Sem o cabeçalho secreto do CloudFront, 403; a regra que encaminha ao chat está em edge.tf.
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "application/json"
      status_code  = "403"
      message_body = jsonencode({ erro = "Acesso direto ao balanceador não é permitido.", codigo = "acesso_direto" })
    }
  }
}
