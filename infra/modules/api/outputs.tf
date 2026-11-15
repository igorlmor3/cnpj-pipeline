output "function_url" {
  description = "Endereco publico da funcao."
  value       = aws_lambda_function_url.api.function_url
}

output "function_name" {
  value = aws_lambda_function.api.function_name
}

output "state_table" {
  value = aws_dynamodb_table.state.name
}

output "param_prefix" {
  description = "Prefixo dos parametros: a chave do provedor vai em <prefixo>/anthropic-api-key e o interruptor em <prefixo>/enabled."
  value       = var.param_prefix
}
