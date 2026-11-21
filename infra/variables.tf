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

variable "api_enabled" {
  description = "Cria a API publica do agente (funcao, endereco, tabela de estado e parametros)."
  type        = bool
  default     = false
}

variable "api_month" {
  description = "Mes de referencia (AAAA-MM) consultado pelo agente. Deve ser o mesmo usado ao gerar o pacote."
  type        = string
  default     = "2026-10"
}

variable "api_months" {
  description = "Meses (AAAA-MM) que o agente pode consultar, incluindo api_month, que e o padrao. Vazio: so api_month."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for m in var.api_months : can(regex("^[0-9]{4}-(0[1-9]|1[0-2])$", m))])
    error_message = "Cada mes de api_months deve ter o formato AAAA-MM."
  }

  validation {
    condition     = length(var.api_months) == 0 || contains(var.api_months, var.api_month)
    error_message = "api_months deve incluir api_month."
  }
}

variable "api_package" {
  description = "Caminho do zip da funcao, relativo a este diretorio."
  type        = string
  default     = "build/api.zip"
}

variable "api_allowed_origins" {
  description = "Origens https do site que podem chamar a API pelo navegador (sem barra final). Vazio bloqueia todas."
  type        = list(string)
  default     = []
}

variable "api_reserved_concurrency" {
  description = "Execucoes simultaneas reservadas para a funcao. Nulo nao reserva. Contas novas tem limite total baixo, e a AWS exige que 10 fiquem sem reserva."
  type        = number
  default     = null
}

variable "api_monthly_budget_usd" {
  description = "Teto de gasto do agente por mes, aplicado pela propria funcao. O limite do workspace no provedor continua sendo a barreira final."
  type        = number
  default     = 4
}

variable "api_alarm_email" {
  description = "E-mail que recebe os alarmes da API (erros, gasto do dia e teto do mes atingido). Vazio: so aparecem no console."
  type        = string
  default     = ""
}

variable "api_daily_cost_alarm_usd" {
  description = "Gasto do agente em um dia (US$) acima do qual o alarme dispara."
  type        = number
  default     = 1
}

variable "ecs_enabled" {
  description = "Nivel 2: cria o cluster, o repositorio de imagens, o papel IAM e os logs para rodar o pipeline no ECS Fargate."
  type        = bool
  default     = false
}

variable "ecs_vcpu" {
  description = "vCPUs da tarefa do pipeline no Fargate (1, 2, 4, 8 ou 16)."
  type        = number
  default     = 4
}

variable "ecs_memory_mib" {
  description = "Memoria da tarefa em MiB, dentro da faixa que o Fargate aceita para as vCPUs (4 vCPUs: 8192 a 30720)."
  type        = number
  default     = 16384
}

variable "ecs_ephemeral_storage_gib" {
  description = "Disco de trabalho da tarefa em GiB (21 a 200). Um mes ocupa cerca de 47 GiB."
  type        = number
  default     = 120
}

variable "ecs_task_timeout_minutes" {
  description = "Tempo maximo de uma execucao; passado o limite, a tarefa se encerra sozinha."
  type        = number
  default     = 300
}

variable "glue_enabled" {
  description = "Experimento: cria o job Spark na AWS Glue que constroi o fato e os marts da gold, para comparar com o DuckDB."
  type        = bool
  default     = false
}

variable "glue_workers" {
  description = "Workers do job Spark (G.1X, 4 vCPUs e 16 GB cada; um deles e o driver). Cada execucao pode usar outro valor."
  type        = number
  default     = 5
}

variable "glue_timeout_minutes" {
  description = "Limite de duracao de uma execucao do job Spark; passado o limite, a Glue a encerra."
  type        = number
  default     = 60
}

variable "experiments_retention_days" {
  description = "Dias que os arquivos de experiments/ (entrada, saida e codigo do experimento Spark) ficam no bucket."
  type        = number
  default     = 30
}
