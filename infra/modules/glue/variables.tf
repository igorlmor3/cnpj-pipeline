variable "name" {
  description = "Nome do job na Glue e prefixo dos recursos"
  type        = string
  default     = "cnpj-gold-spark"
}

variable "bucket_name" {
  description = "Bucket do data lake (entrada, saida e codigo do experimento ficam em experiments/spark/)"
  type        = string
}

variable "script_path" {
  description = "Caminho local do script do job (experiments/glue_spark/spark_gold.py)"
  type        = string
}

variable "models_path" {
  description = "Caminho local do gold_models.py, enviado junto com o script"
  type        = string
}

variable "glue_version" {
  description = "Versao da Glue. A 5.0 roda Spark 3.5, a versao usada nos testes locais"
  type        = string
  default     = "5.0"
}

variable "worker_type" {
  description = "Tipo de worker. O G.1X tem 1 DPU (4 vCPUs e 16 GB)"
  type        = string
  default     = "G.1X"

  validation {
    condition     = contains(["G.1X", "G.2X", "G.4X", "G.8X"], var.worker_type)
    error_message = "worker_type precisa ser G.1X, G.2X, G.4X ou G.8X."
  }
}

variable "number_of_workers" {
  description = "Workers do job (um deles e o driver). Cada execucao pode mudar isso por argumento"
  type        = number
  default     = 5

  validation {
    condition     = var.number_of_workers >= 2
    error_message = "A Glue exige pelo menos 2 workers para um job Spark."
  }
}

variable "timeout_minutes" {
  description = "Limite de duracao de uma execucao; a Glue a encerra depois disso e a cobranca para"
  type        = number
  default     = 60

  validation {
    condition     = var.timeout_minutes > 0 && var.timeout_minutes <= 240
    error_message = "timeout_minutes precisa estar entre 1 e 240."
  }
}
