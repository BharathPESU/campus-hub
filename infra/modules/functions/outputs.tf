output "function_arns" {
  description = "Map of Lambda function names to ARNs"
  value       = { for k, v in aws_lambda_function.functions : k => v.arn }
}

output "function_invoke_arns" {
  description = "Map of Lambda function names to invoke ARNs"
  value       = { for k, v in aws_lambda_function.functions : k => v.invoke_arn }
}

output "function_names" {
  description = "Map of Lambda function names to actual function names"
  value       = { for k, v in aws_lambda_function.functions : k => v.function_name }
}
