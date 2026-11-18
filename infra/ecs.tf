# ---------------------------------------------------------------------------
# Nivel 2: o pipeline roda na nuvem, em uma tarefa do ECS Fargate (cluster, repositorio de imagens,
# papel IAM da tarefa e logs). So e criado com ecs_enabled = true. Depois do apply, envie a imagem
# (veja infra/README.md) e dispare a tarefa com python -m cnpj_pipeline.cloud.run_task.
# O papel da tarefa reaproveita a politica do usuario publicador: mesmo bucket, workgroup e banco,
# sem acesso a mais nada.
# ---------------------------------------------------------------------------

module "ecs" {
  count  = var.ecs_enabled ? 1 : 0
  source = "./modules/ecs"

  bucket_name           = aws_s3_bucket.lake.bucket
  task_policy_json      = data.aws_iam_policy_document.publisher.json
  vcpu                  = var.ecs_vcpu
  memory_mib            = var.ecs_memory_mib
  ephemeral_storage_gib = var.ecs_ephemeral_storage_gib
  task_timeout_minutes  = var.ecs_task_timeout_minutes
}
