data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

locals {
  bucket_arn    = "arn:aws:s3:::${var.bucket_name}"
  root_prefix   = "experiments/spark"
  code_prefix   = "${local.root_prefix}/code"
  input_prefix  = "${local.root_prefix}/input"
  output_prefix = "${local.root_prefix}/output"
  script_key    = "${local.code_prefix}/spark_gold.py"
  models_key    = "${local.code_prefix}/gold_models.py"
}

# ---------------------------------------------------------------------------
# Codigo do job: o script e o gold_models.py (o mesmo SQL da gold) vao para o bucket
# ---------------------------------------------------------------------------

resource "aws_s3_object" "script" {
  bucket = var.bucket_name
  key    = local.script_key
  source = var.script_path
  etag   = filemd5(var.script_path)
}

resource "aws_s3_object" "models" {
  bucket = var.bucket_name
  key    = local.models_key
  source = var.models_path
  etag   = filemd5(var.models_path)
}

# ---------------------------------------------------------------------------
# Papel do job: le a entrada, o codigo e a gold; grava so a saida do experimento
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["glue.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "job" {
  name               = "${var.name}-job"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "job" {
  # Listar o bucket sem filtro de prefixo: o Spark consulta tambem os diretorios acima do caminho que
  # le ou grava, e uma listagem negada volta como erro 403 em vez de "nao existe". Ler e gravar
  # objetos continua restrito aos prefixos abaixo.
  statement {
    sid       = "ListBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [local.bucket_arn]
  }

  statement {
    sid     = "ReadInputCodeAndGold"
    actions = ["s3:GetObject"]
    resources = [
      "${local.bucket_arn}/${local.input_prefix}/*",
      "${local.bucket_arn}/${local.code_prefix}/*",
      "${local.bucket_arn}/gold/*",
    ]
  }

  statement {
    sid = "WriteOutput"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${local.bucket_arn}/${local.output_prefix}/*"]
  }

  statement {
    sid = "Logs"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = ["arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws-glue/*"]
  }
}

resource "aws_iam_role_policy" "job" {
  name   = var.name
  role   = aws_iam_role.job.id
  policy = data.aws_iam_policy_document.job.json
}

# ---------------------------------------------------------------------------
# O job. Nao ha gatilho nem agendamento: ele so roda quando alguem o dispara.
# ---------------------------------------------------------------------------

resource "aws_glue_job" "this" {
  name              = var.name
  role_arn          = aws_iam_role.job.arn
  glue_version      = var.glue_version
  worker_type       = var.worker_type
  number_of_workers = var.number_of_workers
  timeout           = var.timeout_minutes
  max_retries       = 0

  command {
    name            = "glueetl"
    script_location = "s3://${var.bucket_name}/${local.script_key}"
    python_version  = "3"
  }

  default_arguments = {
    "--job-language"                     = "python"
    "--extra-py-files"                   = "s3://${var.bucket_name}/${local.models_key}"
    "--input-dir"                        = "s3://${var.bucket_name}/${local.input_prefix}"
    "--output-dir"                       = "s3://${var.bucket_name}/${local.output_prefix}"
    "--enable-metrics"                   = "true"
    "--enable-continuous-cloudwatch-log" = "true"
  }

  depends_on = [
    aws_s3_object.script,
    aws_s3_object.models,
    aws_iam_role_policy.job,
  ]
}
