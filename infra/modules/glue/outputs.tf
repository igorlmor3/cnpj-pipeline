output "job_name" {
  value = aws_glue_job.this.name
}

output "input_uri" {
  description = "Onde enviar a entrada do experimento (make_input.py)"
  value       = "s3://${var.bucket_name}/${local.input_prefix}"
}

output "output_uri" {
  value = "s3://${var.bucket_name}/${local.output_prefix}"
}
