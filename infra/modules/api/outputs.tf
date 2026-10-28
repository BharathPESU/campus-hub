output "api_endpoint" {
  description = "Base URL of the HTTP API Gateway"
  value       = aws_apigatewayv2_api.http.api_endpoint
}

output "api_domain" {
  description = "Domain name of the HTTP API Gateway (without https://)"
  value       = replace(aws_apigatewayv2_api.http.api_endpoint, "https://", "")
}

output "api_id" {
  description = "ID of the HTTP API Gateway"
  value       = aws_apigatewayv2_api.http.id
}

output "execution_arn" {
  description = "Execution ARN of the HTTP API Gateway"
  value       = aws_apigatewayv2_api.http.execution_arn
}
