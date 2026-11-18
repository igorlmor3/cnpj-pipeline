# ---------------------------------------------------------------------------
# Observabilidade da API: consultas salvas no Logs Insights, metricas, alarmes e um painel.
#
# Tudo parte do registro de uma linha por pergunta que a funcao escreve na saida padrao
# (agent/../api/handler.py, evento "ask"): so contagens, tempos, tokens, custo, status e o
# identificador anonimo do visitante (8 caracteres do HMAC). Pergunta, SQL, resposta, chave e IP
# nunca entram no registro, entao nada aqui os enxerga. Um teste confere que cada campo usado
# nas consultas e nos filtros existe no registro e que nenhum campo sensivel e usado.
# ---------------------------------------------------------------------------

locals {
  log_group_name = aws_cloudwatch_log_group.api.name
  metric_ns      = "CnpjPipeline/Api"
}

# ---------------------------------------------------------------------------
# Consultas salvas (CloudWatch > Logs Insights > Consultas salvas)
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_query_definition" "asks_by_status" {
  name            = "${var.name}/api/perguntas-por-dia-e-status"
  log_group_names = [local.log_group_name]

  query_string = <<-EOT
    filter event = "ask"
    | stats count(*) as perguntas by bin(1d) as dia, status
    | sort dia desc
  EOT
}

resource "aws_cloudwatch_query_definition" "latency" {
  name            = "${var.name}/api/latencia-das-respostas"
  log_group_names = [local.log_group_name]

  query_string = <<-EOT
    filter event = "ask" and status = "answered"
    | stats count(*) as respostas, pct(ms, 50) as p50_ms, pct(ms, 95) as p95_ms, pct(ms, 99) as p99_ms by bin(1d) as dia
    | sort dia desc
  EOT
}

resource "aws_cloudwatch_query_definition" "cost" {
  name            = "${var.name}/api/custo-e-tokens-por-dia"
  log_group_names = [local.log_group_name]

  query_string = <<-EOT
    filter event = "ask"
    | stats count(*) as perguntas, sum(cost_usd) as custo_usd, avg(cost_usd) as custo_medio_usd, sum(input_tokens) as tokens_entrada, sum(output_tokens) as tokens_saida by bin(1d) as dia
    | sort dia desc
  EOT
}

resource "aws_cloudwatch_query_definition" "fidelity" {
  name            = "${var.name}/api/fidelidade-dos-numeros"
  log_group_names = [local.log_group_name]

  query_string = <<-EOT
    filter event = "ask" and status = "answered"
    | stats count(*) as respostas, sum(numbers_checked) as numeros_verificados, sum(unsupported_numbers) as numeros_sem_apoio by bin(1d) as dia
    | sort dia desc
  EOT
}

resource "aws_cloudwatch_query_definition" "tools" {
  name            = "${var.name}/api/uso-das-ferramentas"
  log_group_names = [local.log_group_name]

  query_string = <<-EOT
    filter event = "ask" and status = "answered"
    | stats avg(queries) as consultas_sql_por_pergunta, avg(doc_searches) as buscas_docs_por_pergunta, avg(llm_calls) as chamadas_modelo_por_pergunta, sum(bytes_scanned) as bytes_lidos_no_athena by bin(1d) as dia
    | sort dia desc
  EOT
}

resource "aws_cloudwatch_query_definition" "refusals" {
  name            = "${var.name}/api/recusas-por-status"
  log_group_names = [local.log_group_name]

  query_string = <<-EOT
    filter event = "ask" and status != "answered"
    | stats count(*) as ocorrencias by status
    | sort ocorrencias desc
  EOT
}

resource "aws_cloudwatch_query_definition" "limits" {
  name            = "${var.name}/api/limites-por-motivo"
  log_group_names = [local.log_group_name]

  query_string = <<-EOT
    filter event = "ask" and status = "limited"
    | stats count(*) as ocorrencias by reason
    | sort ocorrencias desc
  EOT
}

# ---------------------------------------------------------------------------
# Metricas a partir do registro (filtros de metrica sobre JSON)
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_metric_filter" "asks" {
  name           = "${var.name}-api-asks"
  log_group_name = local.log_group_name
  pattern        = "{ $.event = \"ask\" }"

  metric_transformation {
    name      = "Asks"
    namespace = local.metric_ns
    value     = "1"
    unit      = "Count"
  }
}

resource "aws_cloudwatch_log_metric_filter" "errors" {
  name           = "${var.name}-api-errors"
  log_group_name = local.log_group_name
  pattern        = "{ $.event = \"ask\" && ($.status = \"llm_error\" || $.status = \"internal_error\") }"

  metric_transformation {
    name      = "Errors"
    namespace = local.metric_ns
    value     = "1"
    unit      = "Count"
  }
}

resource "aws_cloudwatch_log_metric_filter" "budget_hits" {
  name           = "${var.name}-api-budget-hits"
  log_group_name = local.log_group_name
  pattern        = "{ $.event = \"ask\" && $.status = \"budget\" }"

  metric_transformation {
    name      = "BudgetHits"
    namespace = local.metric_ns
    value     = "1"
    unit      = "Count"
  }
}

resource "aws_cloudwatch_log_metric_filter" "cost" {
  name           = "${var.name}-api-cost"
  log_group_name = local.log_group_name
  pattern        = "{ $.event = \"ask\" && $.cost_usd > 0 }"

  metric_transformation {
    name      = "CostUsd"
    namespace = local.metric_ns
    value     = "$.cost_usd"
    unit      = "None"
  }
}

resource "aws_cloudwatch_log_metric_filter" "unsupported" {
  name           = "${var.name}-api-unsupported-numbers"
  log_group_name = local.log_group_name
  pattern        = "{ $.event = \"ask\" && $.unsupported_numbers > 0 }"

  metric_transformation {
    name      = "AnswersWithUnsupportedNumbers"
    namespace = local.metric_ns
    value     = "1"
    unit      = "Count"
  }
}

# ---------------------------------------------------------------------------
# Alarmes. Sem alarm_email o alarme so aparece no console; com ele, um topico SNS envia um
# e-mail (a AWS manda um pedido de confirmacao que precisa ser aceito uma vez).
# ---------------------------------------------------------------------------

resource "aws_sns_topic" "alarms" {
  count = var.alarm_email != "" ? 1 : 0
  name  = "${var.name}-api-alarms"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alarms[0].arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

locals {
  alarm_actions = [for topic in aws_sns_topic.alarms : topic.arn]
}

resource "aws_cloudwatch_metric_alarm" "daily_cost" {
  alarm_name          = "${var.name}-api-daily-cost"
  alarm_description   = "Gasto do agente com o modelo em um dia acima do limite de alerta (o teto do mes e aplicado pela propria funcao)."
  namespace           = local.metric_ns
  metric_name         = "CostUsd"
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = var.daily_cost_alarm_usd
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "errors" {
  alarm_name          = "${var.name}-api-errors"
  alarm_description   = "Erros do modelo ou internos em perguntas: tres ou mais em uma hora."
  namespace           = local.metric_ns
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 3
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "budget_hit" {
  alarm_name          = "${var.name}-api-budget-hit"
  alarm_description   = "O teto de gasto do mes foi atingido e o agente deixou de responder perguntas novas."
  namespace           = local.metric_ns
  metric_name         = "BudgetHits"
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

# ---------------------------------------------------------------------------
# Painel
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_dashboard" "api" {
  dashboard_name = "${var.name}-api"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 8
        height = 6
        properties = {
          title  = "Perguntas, erros e teto atingido (por dia)"
          region = local.region
          view   = "timeSeries"
          stat   = "Sum"
          period = 86400
          metrics = [
            [local.metric_ns, "Asks"],
            [local.metric_ns, "Errors"],
            [local.metric_ns, "BudgetHits"],
          ]
        }
      },
      {
        type   = "metric"
        x      = 8
        y      = 0
        width  = 8
        height = 6
        properties = {
          title   = "Gasto com o modelo (US$ por dia)"
          region  = local.region
          view    = "timeSeries"
          stat    = "Sum"
          period  = 86400
          metrics = [[local.metric_ns, "CostUsd"]]
        }
      },
      {
        type   = "metric"
        x      = 16
        y      = 0
        width  = 8
        height = 6
        properties = {
          title   = "Respostas com numero sem apoio (por dia)"
          region  = local.region
          view    = "timeSeries"
          stat    = "Sum"
          period  = 86400
          metrics = [[local.metric_ns, "AnswersWithUnsupportedNumbers"]]
        }
      },
      {
        type   = "log"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Latencia das respostas (ms)"
          region = local.region
          view   = "table"
          query  = "SOURCE '${local.log_group_name}' | filter event = \"ask\" and status = \"answered\" | stats count(*) as respostas, pct(ms, 50) as p50_ms, pct(ms, 95) as p95_ms by bin(1d) as dia | sort dia desc"
        }
      },
      {
        type   = "log"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Perguntas por status"
          region = local.region
          view   = "table"
          query  = "SOURCE '${local.log_group_name}' | filter event = \"ask\" | stats count(*) as perguntas by status | sort perguntas desc"
        }
      },
    ]
  })
}
