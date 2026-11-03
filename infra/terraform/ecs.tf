# Cluster ECS sobre EC2: launch template + ASG + capacity provider com managed scaling.

resource "aws_service_discovery_http_namespace" "sc" {
  name        = "${local.p}.local"
  description = "Service Connect do ${local.p}"
}

resource "aws_ecs_cluster" "main" {
  name = local.cluster
  service_connect_defaults {
    namespace = aws_service_discovery_http_namespace.sc.arn
  }
  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

data "aws_ssm_parameter" "ecs_ami" {
  name = "/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended/image_id"
}

resource "aws_launch_template" "ecs" {
  name_prefix   = "${local.p}-ecs-"
  image_id      = data.aws_ssm_parameter.ecs_ami.value
  instance_type = var.instance_type
  iam_instance_profile {
    arn = aws_iam_instance_profile.instance.arn
  }
  vpc_security_group_ids = [aws_security_group.ecs.id]
  metadata_options {
    http_tokens                 = "required"
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 2
  }
  user_data = base64encode(<<-UD
    #!/bin/bash
    cat >> /etc/ecs/ecs.config <<CFG
    ECS_CLUSTER=${local.cluster}
    ECS_ENABLE_TASK_IAM_ROLE=true
    ECS_ENABLE_TASK_IAM_ROLE_NETWORK_HOST=true
    ECS_CONTAINER_STOP_TIMEOUT=30s
    CFG
  UD
  )
  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${local.p}-ecs", Project = var.project }
  }
}

resource "aws_autoscaling_group" "ecs" {
  name                  = "${local.p}-ecs-asg"
  min_size              = var.asg_min
  max_size              = var.asg_max
  desired_capacity      = var.asg_min
  vpc_zone_identifier   = aws_subnet.app[*].id
  protect_from_scale_in = true # exigido pela managed termination protection
  force_delete          = true # permite destroy com instâncias protegidas

  launch_template {
    id      = aws_launch_template.ecs.id
    version = aws_launch_template.ecs.latest_version
  }
  tag {
    key                 = "AmazonECSManaged"
    value               = "true"
    propagate_at_launch = true
  }
  tag {
    key                 = "Project"
    value               = var.project
    propagate_at_launch = true
  }
  lifecycle {
    ignore_changes = [desired_capacity] # quem ajusta é o capacity provider
  }
}

resource "aws_ecs_capacity_provider" "cp" {
  name = "${local.p}-cp"
  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.ecs.arn
    managed_termination_protection = "ENABLED"
    managed_scaling {
      status                    = "ENABLED"
      target_capacity           = 90
      minimum_scaling_step_size = 1
      maximum_scaling_step_size = 2
    }
  }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = [aws_ecs_capacity_provider.cp.name]
  default_capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.cp.name
    weight            = 1
  }
}
