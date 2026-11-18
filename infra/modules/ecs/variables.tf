variable "name" {
  description = "Prefixo dos nomes dos recursos. Tambem e o nome do cluster, do repositorio de imagens e da definicao da tarefa."
  type        = string
  default     = "cnpj-pipeline"
}

variable "bucket_name" {
  description = "Bucket S3 do data lake, onde a tarefa publica a gold (vira CNPJ_S3_BUCKET)."
  type        = string
}

variable "task_policy_json" {
  description = "Politica IAM do papel da tarefa. Use a mesma do usuario publicador: S3, Athena e Glue, so deste bucket, workgroup e banco."
  type        = string
}

variable "vcpu" {
  description = "vCPUs da tarefa (1, 2, 4, 8 ou 16)."
  type        = number
  default     = 4

  validation {
    condition     = contains([1, 2, 4, 8, 16], var.vcpu)
    error_message = "O Fargate aceita 1, 2, 4, 8 ou 16 vCPUs."
  }
}

variable "memory_mib" {
  description = "Memoria da tarefa em MiB. O Fargate limita a faixa conforme as vCPUs (4 vCPUs: 8192 a 30720)."
  type        = number
  default     = 16384
}

variable "ephemeral_storage_gib" {
  description = "Disco de trabalho da tarefa em GiB (21 a 200). Um mes ocupa cerca de 47 GiB (zips, CSV, bronze, silver e gold)."
  type        = number
  default     = 120

  validation {
    condition     = var.ephemeral_storage_gib >= 21 && var.ephemeral_storage_gib <= 200
    error_message = "O disco da tarefa no Fargate vai de 21 a 200 GiB."
  }
}

variable "task_timeout_minutes" {
  description = "Tempo maximo da execucao. Passado o limite, a tarefa se encerra sozinha (o ECS nao limita a duracao)."
  type        = number
  default     = 300
}

variable "min_free_gib" {
  description = "Espaco livre minimo no disco de trabalho para a tarefa comecar."
  type        = number
  default     = 60
}

variable "image_tag" {
  description = "Tag da imagem usada pela tarefa."
  type        = string
  default     = "latest"
}

variable "log_retention_days" {
  description = "Dias que os logs da tarefa ficam no CloudWatch."
  type        = number
  default     = 14
}
