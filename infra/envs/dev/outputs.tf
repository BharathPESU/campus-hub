output "region" {
  description = "AWS region for the dev environment"
  value       = var.region
}

output "table_name" {
  description = "Name of the DynamoDB table"
  value       = module.data.table_name
}

output "table_arn" {
  description = "ARN of the DynamoDB table"
  value       = module.data.table_arn
}

output "user_pool_id" {
  description = "ID of the Cognito user pool"
  value       = module.auth.user_pool_id
}

output "app_client_id" {
  description = "ID of the Cognito user pool client"
  value       = module.auth.client_id
}

output "cognito_domain_url" {
  description = "Hosted UI login URL"
  value       = module.auth.domain_url
}

output "api_endpoint" {
  description = "Base URL of the HTTP API Gateway"
  value       = module.api.api_endpoint
}

