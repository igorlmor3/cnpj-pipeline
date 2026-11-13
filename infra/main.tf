data "aws_caller_identity" "current" {}

locals {
  glue_database  = "cnpj"
  workgroup_name = "cnpj-pipeline"
  results_prefix = "athena-results/"
}

# ---------------------------------------------------------------------------
# Data lake: um bucket privado, criptografado, com limpeza de residuos
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "lake" {
  bucket = var.bucket_name

  # Os dados sao derivados e podem ser regenerados pelo pipeline, entao `terraform destroy`
  # pode esvaziar o bucket. Remova esta linha se um dia o bucket guardar algo insubstituivel.
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id

  rule {
    id     = "expire-athena-results"
    status = "Enabled"

    filter {
      prefix = local.results_prefix
    }

    expiration {
      days = var.athena_results_retention_days
    }
  }

  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# ---------------------------------------------------------------------------
# Catalogo e consultas: Glue (catalogo) + Athena (SQL) com limite de leitura
# As tabelas sao criadas por `python -m cnpj_pipeline.cloud.athena create-tables`.
# ---------------------------------------------------------------------------

resource "aws_glue_catalog_database" "cnpj" {
  name        = local.glue_database
  description = "Camadas silver e gold do pipeline CNPJ"
}

resource "aws_athena_workgroup" "cnpj" {
  name          = local.workgroup_name
  force_destroy = true

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = false
    bytes_scanned_cutoff_per_query     = var.athena_scan_limit_gb * 1024 * 1024 * 1024

    result_configuration {
      output_location = "s3://${aws_s3_bucket.lake.bucket}/${local.results_prefix}"

      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
}

# ---------------------------------------------------------------------------
# Usuario do pipeline com menor privilegio: so este bucket, este workgroup e
# este banco do Glue. As chaves de acesso sao criadas manualmente no console
# (nao pelo Terraform), para que nunca fiquem gravadas no arquivo de estado.
# ---------------------------------------------------------------------------

resource "aws_iam_user" "publisher" {
  name = "cnpj-pipeline-publisher"
}

data "aws_iam_policy_document" "publisher" {
  statement {
    sid       = "ListBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation", "s3:ListBucketMultipartUploads"]
    resources = [aws_s3_bucket.lake.arn]
  }

  statement {
    sid = "ReadWriteObjects"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${aws_s3_bucket.lake.arn}/*"]
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
    resources = [aws_athena_workgroup.cnpj.arn]
  }

  statement {
    sid = "GlueCatalogRead"
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
    ]
    resources = [
      "arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:catalog",
      aws_glue_catalog_database.cnpj.arn,
    ]
  }

  statement {
    sid = "GlueTables"
    actions = [
      "glue:GetTable",
      "glue:GetTables",
      "glue:CreateTable",
      "glue:UpdateTable",
      "glue:DeleteTable",
      "glue:GetPartition",
      "glue:GetPartitions",
    ]
    resources = [
      "arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:catalog",
      aws_glue_catalog_database.cnpj.arn,
      "arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:table/${local.glue_database}/*",
    ]
  }
}

resource "aws_iam_user_policy" "publisher" {
  name   = "cnpj-pipeline-publisher"
  user   = aws_iam_user.publisher.name
  policy = data.aws_iam_policy_document.publisher.json
}

# ---------------------------------------------------------------------------
# Alerta de orcamento
# ---------------------------------------------------------------------------

resource "aws_budgets_budget" "monthly" {
  name         = "cnpj-pipeline-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}
