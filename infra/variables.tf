variable "region" {
  description = "Regiao da AWS. us-east-1 costuma ser a de menor custo."
  type        = string
  default     = "us-east-1"
}

variable "profile" {
  description = "Perfil do AWS CLI com permissao de administrador, usado so para aplicar o Terraform."
  type        = string
  default     = "cnpj-admin"
}

variable "bucket_name" {
  description = "Nome do bucket S3 do data lake. Precisa ser unico em toda a AWS (por exemplo cnpj-pipeline-igor-2026)."
  type        = string
}

variable "alert_email" {
  description = "E-mail que recebe os alertas de orcamento."
  type        = string
}

variable "monthly_budget_usd" {
  description = "Orcamento mensal em dolares. Os alertas disparam em 80% do gasto real e 100% do previsto."
  type        = number
  default     = 5
}

variable "athena_scan_limit_gb" {
  description = "Limite de dados lidos por consulta no Athena, em GB. Consultas acima do limite sao canceladas."
  type        = number
  default     = 5
}

variable "athena_results_retention_days" {
  description = "Dias que os resultados de consultas do Athena ficam no bucket antes de serem apagados."
  type        = number
  default     = 7
}
