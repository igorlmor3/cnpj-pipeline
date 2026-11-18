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

output "api_url" {
  description = "Endereco publico da API do agente (nulo se api_enabled for false)."
  value       = one(module.api[*].function_url)
}

output "api_param_prefix" {
  description = "Prefixo dos parametros da API no Parameter Store."
  value       = one(module.api[*].param_prefix)
}

output "api_dashboard" {
  description = "Nome do painel do CloudWatch da API (nulo se api_enabled for false)."
  value       = one(module.api[*].dashboard_name)
}

output "ecs_cluster" {
  description = "Cluster do ECS (nulo se ecs_enabled for false). Use em CNPJ_ECS_CLUSTER."
  value       = one(module.ecs[*].cluster_name)
}

output "ecs_task_definition" {
  description = "Definicao da tarefa do pipeline. Use em CNPJ_ECS_TASK_DEFINITION."
  value       = one(module.ecs[*].task_definition)
}

output "ecs_subnets" {
  description = "Sub-redes da tarefa. Use em CNPJ_ECS_SUBNETS, separadas por virgula."
  value       = one(module.ecs[*].subnets)
}

output "ecs_security_group" {
  description = "Grupo de seguranca da tarefa, sem nenhuma entrada. Use em CNPJ_ECS_SECURITY_GROUP."
  value       = one(module.ecs[*].security_group_id)
}

output "ecs_repository_url" {
  description = "Repositorio de imagens do pipeline no ECR."
  value       = one(module.ecs[*].repository_url)
}

output "ecs_log_group" {
  description = "Grupo de logs da tarefa no CloudWatch."
  value       = one(module.ecs[*].log_group)
}
