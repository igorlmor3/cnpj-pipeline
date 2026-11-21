# ---------------------------------------------------------------------------
# Experimento: o fato e os marts da gold construidos com Spark na AWS Glue, para comparar tempo e
# custo com o DuckDB (veja experiments/glue_spark/README.md). So e criado com glue_enabled = true.
# O job nao tem agendamento: roda quando e disparado por experiments/glue_spark/run_job.py.
# ---------------------------------------------------------------------------

module "glue" {
  count  = var.glue_enabled ? 1 : 0
  source = "./modules/glue"

  bucket_name       = aws_s3_bucket.lake.bucket
  script_path       = "${path.module}/../experiments/glue_spark/spark_gold.py"
  models_path       = "${path.module}/../src/cnpj_pipeline/gold_models.py"
  number_of_workers = var.glue_workers
  timeout_minutes   = var.glue_timeout_minutes
}
