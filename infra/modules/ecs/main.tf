data "aws_region" "current" {}

locals {
  cluster_name   = var.name
  container_name = "pipeline" # o disparador (cloud/run_task.py) sobrescreve o container por este nome
  log_group      = "/ecs/${var.name}"

  # Faixas de memoria que o Fargate aceita para cada quantidade de vCPUs (MiB).
  memory_range = {
    1  = { min = 2048, max = 8192 }
    2  = { min = 4096, max = 16384 }
    4  = { min = 8192, max = 30720 }
    8  = { min = 16384, max = 61440 }
    16 = { min = 32768, max = 122880 }
  }
}

# ---------------------------------------------------------------------------
# Imagem: repositorio privado, sem custo parado alem do armazenamento das ultimas versoes
# ---------------------------------------------------------------------------

resource "aws_ecr_repository" "this" {
  name                 = var.name
  image_tag_mutability = "MUTABLE"

  # A imagem sai do codigo do repositorio e pode ser refeita, entao o destroy pode esvazia-lo.
  force_delete = true

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  repository = aws_ecr_repository.this.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Guarda so as 3 imagens mais recentes"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 3
      }
      action = { type = "expire" }
    }]
  })
}

# ---------------------------------------------------------------------------
# Cluster e logs
# ---------------------------------------------------------------------------

resource "aws_ecs_cluster" "this" {
  name = local.cluster_name
}

resource "aws_cloudwatch_log_group" "task" {
  name              = local.log_group
  retention_in_days = var.log_retention_days
}

# ---------------------------------------------------------------------------
# Papeis. O da execucao so puxa a imagem e escreve logs. O da tarefa e o que o pipeline usa
# em tempo de execucao: no lugar de chaves de acesso, as credenciais temporarias dele.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.name}-task-execution"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy" "task" {
  name   = "${var.name}-task"
  role   = aws_iam_role.task.id
  policy = var.task_policy_json
}

# ---------------------------------------------------------------------------
# Rede: VPC padrao, sem NAT Gateway (que cobra por hora). A tarefa sai para a internet por um IP
# publico, so para baixar da Receita e falar com a AWS, e o grupo de seguranca nao aceita nenhuma entrada.
# ---------------------------------------------------------------------------

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_security_group" "task" {
  name        = "${var.name}-task"
  description = "Tarefa do pipeline: nenhuma entrada, saida livre."
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.task.id
  description       = "Saida para a Receita Federal, o S3, o Athena e o Glue"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# ---------------------------------------------------------------------------
# Definicao da tarefa
# ---------------------------------------------------------------------------

resource "aws_ecs_task_definition" "this" {
  family                   = var.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.vcpu * 1024)
  memory                   = tostring(var.memory_mib)
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  ephemeral_storage {
    size_in_gib = var.ephemeral_storage_gib
  }

  container_definitions = jsonencode([{
    name      = local.container_name
    image     = "${aws_ecr_repository.this.repository_url}:${var.image_tag}"
    essential = true

    environment = [
      { name = "CNPJ_S3_BUCKET", value = var.bucket_name },
      { name = "CNPJ_AWS_REGION", value = data.aws_region.current.region },
      { name = "CNPJ_TASK_TIMEOUT_MINUTES", value = tostring(var.task_timeout_minutes) },
      { name = "CNPJ_MIN_FREE_GIB", value = tostring(var.min_free_gib) },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.task.name
        "awslogs-region"        = data.aws_region.current.region
        "awslogs-stream-prefix" = "pipeline"
      }
    }
  }])

  lifecycle {
    precondition {
      condition = (
        var.memory_mib >= local.memory_range[tostring(var.vcpu)].min &&
        var.memory_mib <= local.memory_range[tostring(var.vcpu)].max
      )
      error_message = "Com ${var.vcpu} vCPUs o Fargate aceita de ${local.memory_range[tostring(var.vcpu)].min} a ${local.memory_range[tostring(var.vcpu)].max} MiB de memoria."
    }
  }
}
