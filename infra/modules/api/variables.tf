variable "name" {
  description = "Prefixo dos nomes dos recursos."
  type        = string
  default     = "cnpj-pipeline"
}

variable "month" {
  description = "Mes de referencia (AAAA-MM) que o agente consulta. Precisa ser o mesmo do arquivo de colunas do pacote."
  type        = string
}

variable "months" {
  description = "Meses (AAAA-MM) que o agente pode consultar, incluindo month. Vazio: so month."
  type        = list(string)
  default     = []
}

variable "package_path" {
  description = "Caminho do zip da funcao, gerado por python -m cnpj_pipeline.api.package."
  type        = string
}

variable "bucket_arn" {
  description = "ARN do bucket do data lake."
  type        = string
}

variable "gold_prefix" {
  description = "Prefixo da camada gold no bucket. E a unica parte do bucket que a funcao pode ler."
  type        = string
  default     = "gold/"
}

variable "results_prefix" {
  description = "Prefixo dos resultados do Athena no bucket."
  type        = string
  default     = "athena-results/"
}

variable "athena_workgroup_name" {
  description = "Workgroup do Athena usado pela funcao."
  type        = string
}

variable "glue_database" {
  description = "Banco do Glue com as tabelas da gold."
  type        = string
}

variable "allowed_origins" {
  description = "Origens https que podem chamar a funcao pelo navegador. Vazio bloqueia todas."
  type        = list(string)
  default     = []
}

variable "param_prefix" {
  description = "Prefixo dos parametros no Parameter Store."
  type        = string
  default     = "/cnpj-pipeline/api"
}

variable "memory_mb" {
  type    = number
  default = 512
}

variable "timeout_s" {
  description = "Tempo maximo de uma chamada. Precisa ser maior que o prazo por pergunta do agente."
  type        = number
  default     = 90
}

variable "reserved_concurrency" {
  description = "Execucoes simultaneas reservadas. Nulo nao reserva nada. Contas novas tem um limite total baixo, e a AWS exige que 10 fiquem sem reserva."
  type        = number
  default     = null
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "per_minute" {
  type    = number
  default = 3
}

variable "per_day" {
  type    = number
  default = 15
}

variable "global_per_day" {
  type    = number
  default = 150
}

variable "monthly_budget_usd" {
  description = "Teto de gasto do agente no mes, controlado pela propria funcao. O limite do workspace no provedor continua sendo a barreira final."
  type        = number
  default     = 4
}

variable "alarm_email" {
  description = "E-mail que recebe os alarmes (erros, gasto do dia e teto do mes atingido). Vazio: os alarmes existem, mas so aparecem no console. A AWS envia um pedido de confirmacao que precisa ser aceito uma vez."
  type        = string
  default     = ""
}

variable "daily_cost_alarm_usd" {
  description = "Gasto com o modelo em um dia (US$) acima do qual o alarme dispara. O teto do mes continua sendo aplicado pela funcao."
  type        = number
  default     = 1
}
