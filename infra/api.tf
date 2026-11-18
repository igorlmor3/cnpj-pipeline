# ---------------------------------------------------------------------------
# API publica do agente de texto para SQL (funcao + endereco + estado + parametros).
# So e criada com api_enabled = true. Antes, gere o pacote:
#   python -m cnpj_pipeline.api.package --month <AAAA-MM>
# ---------------------------------------------------------------------------

module "api" {
  count  = var.api_enabled ? 1 : 0
  source = "./modules/api"

  month                 = var.api_month
  months                = var.api_months
  package_path          = "${path.root}/${var.api_package}"
  bucket_arn            = aws_s3_bucket.lake.arn
  results_prefix        = local.results_prefix
  athena_workgroup_name = aws_athena_workgroup.cnpj.name
  glue_database         = aws_glue_catalog_database.cnpj.name
  allowed_origins       = var.api_allowed_origins
  reserved_concurrency  = var.api_reserved_concurrency
  monthly_budget_usd    = var.api_monthly_budget_usd
  alarm_email           = var.api_alarm_email
  daily_cost_alarm_usd  = var.api_daily_cost_alarm_usd
}
