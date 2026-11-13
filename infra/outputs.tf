output "bucket" {
  description = "Bucket do data lake (use em CNPJ_S3_BUCKET)."
  value       = aws_s3_bucket.lake.bucket
}

output "glue_database" {
  value = aws_glue_catalog_database.cnpj.name
}

output "athena_workgroup" {
  value = aws_athena_workgroup.cnpj.name
}

output "publisher_user" {
  description = "Usuario IAM do pipeline. Crie a chave de acesso dele no console."
  value       = aws_iam_user.publisher.name
}
