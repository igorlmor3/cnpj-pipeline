data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  function_name = "${var.name}-api"
  account_id    = data.aws_caller_identity.current.account_id
  region        = data.aws_region.current.region
}

# ---------------------------------------------------------------------------
# Estado compartilhado entre as execucoes: contadores de limite e gasto do mes.
# Os itens de contador expiram sozinhos pelo atributo expires_at (TTL).
# ---------------------------------------------------------------------------

resource "aws_dynamodb_table" "state" {
  name         = "${var.name}-api-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"

  attribute {
    name = "pk"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

# ---------------------------------------------------------------------------
# Parametros. A chave do provedor NAO e criada aqui: ela e gravada por fora, com a CLI,
# para nunca entrar no arquivo de estado do Terraform.
# ---------------------------------------------------------------------------

# Interruptor. Nasce desligado; ligar e desligar e feito pela CLI, e o Terraform nao
# volta o valor (ignore_changes).
resource "aws_ssm_parameter" "enabled" {
  name        = "${var.param_prefix}/enabled"
  description = "Interruptor da API do agente: so o valor true liga."
  type        = "String"
  value       = "false"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "random_password" "salt" {
  length  = 48
  special = false
}

# Sal do identificador do visitante: o IP nunca e gravado, so o HMAC dele com este sal.
resource "aws_ssm_parameter" "salt" {
  name        = "${var.param_prefix}/salt"
  description = "Sal do identificador anonimo do visitante."
  type        = "SecureString"
  value       = random_password.salt.result
}

# ---------------------------------------------------------------------------
# Funcao
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${local.function_name}"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api" {
  name               = "${local.function_name}-role"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "api" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.api.arn}:*"]
  }

  statement {
    sid = "AthenaWorkgroup"
    actions = [
      "athena:StartQueryExecution",
      "athena:StopQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:GetWorkGroup",
    ]
    resources = ["arn:aws:athena:${local.region}:${local.account_id}:workgroup/${var.athena_workgroup_name}"]
  }

  statement {
    sid = "GlueCatalogRead"
    actions = [
      "glue:GetDatabase",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetPartition",
      "glue:GetPartitions",
      "glue:BatchGetPartition",
    ]
    resources = [
      "arn:aws:glue:${local.region}:${local.account_id}:catalog",
      "arn:aws:glue:${local.region}:${local.account_id}:database/${var.glue_database}",
      "arn:aws:glue:${local.region}:${local.account_id}:table/${var.glue_database}/*",
    ]
  }

  statement {
    sid       = "ListBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation", "s3:ListBucketMultipartUploads"]
    resources = [var.bucket_arn]
  }

  # So a gold e os resultados do Athena. A silver, que tem nomes de socios, fica fora.
  statement {
    sid       = "ReadGold"
    actions   = ["s3:GetObject"]
    resources = ["${var.bucket_arn}/${var.gold_prefix}*"]
  }

  statement {
    sid = "AthenaResults"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${var.bucket_arn}/${var.results_prefix}*"]
  }

  statement {
    sid       = "State"
    actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.state.arn]
  }

  statement {
    sid       = "Parameters"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${local.region}:${local.account_id}:parameter${var.param_prefix}/*"]
  }
}

resource "aws_iam_role_policy" "api" {
  name   = local.function_name
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

resource "aws_lambda_function" "api" {
  function_name = local.function_name
  role          = aws_iam_role.api.arn
  runtime       = "python3.12"
  architectures = ["x86_64"]
  handler       = "cnpj_pipeline.api.handler.lambda_handler"

  filename         = var.package_path
  source_code_hash = filebase64sha256(var.package_path)

  memory_size                    = var.memory_mb
  timeout                        = var.timeout_s
  reserved_concurrent_executions = var.reserved_concurrency

  environment {
    variables = {
      CNPJ_API_MONTH              = var.month
      CNPJ_API_MONTHS             = join(",", var.months)
      CNPJ_API_DATABASE           = var.glue_database
      CNPJ_API_WORKGROUP          = var.athena_workgroup_name
      CNPJ_API_TABLE              = aws_dynamodb_table.state.name
      CNPJ_API_PARAM_PREFIX       = var.param_prefix
      CNPJ_API_ALLOWED_ORIGINS    = join(",", var.allowed_origins)
      CNPJ_API_PER_MINUTE         = tostring(var.per_minute)
      CNPJ_API_PER_DAY            = tostring(var.per_day)
      CNPJ_API_GLOBAL_PER_DAY     = tostring(var.global_per_day)
      CNPJ_API_MONTHLY_BUDGET_USD = tostring(var.monthly_budget_usd)
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.api,
    aws_iam_role_policy.api,
  ]
}

# ---------------------------------------------------------------------------
# Endereco publico (Function URL), sem autenticacao da AWS: quem decide e a propria funcao
# (origem, limites, orcamento e interruptor).
# ---------------------------------------------------------------------------

resource "aws_lambda_function_url" "api" {
  function_name      = aws_lambda_function.api.function_name
  authorization_type = "NONE"

  dynamic "cors" {
    for_each = length(var.allowed_origins) > 0 ? [1] : []

    content {
      allow_origins = var.allowed_origins
      allow_methods = ["GET", "POST"]
      allow_headers = ["content-type"]
      max_age       = 3600
    }
  }
}

resource "aws_lambda_permission" "url_invoke" {
  statement_id           = "AllowPublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.api.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "url_invoke_function" {
  statement_id             = "AllowPublicInvokeViaFunctionUrl"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.api.function_name
  principal                = "*"
  invoked_via_function_url = true
}
