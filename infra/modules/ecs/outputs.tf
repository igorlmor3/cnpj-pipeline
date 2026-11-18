output "cluster_name" {
  description = "Cluster do ECS (use em CNPJ_ECS_CLUSTER)."
  value       = aws_ecs_cluster.this.name
}

output "task_definition" {
  description = "Familia da definicao da tarefa; o ECS usa a revisao mais recente (use em CNPJ_ECS_TASK_DEFINITION)."
  value       = aws_ecs_task_definition.this.family
}

output "subnets" {
  description = "Sub-redes da VPC padrao (use em CNPJ_ECS_SUBNETS, separadas por virgula)."
  value       = sort(data.aws_subnets.default.ids)
}

output "security_group_id" {
  description = "Grupo de seguranca da tarefa, sem nenhuma entrada (use em CNPJ_ECS_SECURITY_GROUP)."
  value       = aws_security_group.task.id
}

output "repository_url" {
  description = "Endereco do repositorio de imagens no ECR."
  value       = aws_ecr_repository.this.repository_url
}

output "repository_name" {
  value = aws_ecr_repository.this.name
}

output "log_group" {
  description = "Grupo de logs da tarefa no CloudWatch."
  value       = aws_cloudwatch_log_group.task.name
}
