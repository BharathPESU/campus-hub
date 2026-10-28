output "table_name" {
  description = "Name of the DynamoDB table"
  value       = aws_dynamodb_table.this.name
}

output "table_arn" {
  description = "ARN of the DynamoDB table"
  value       = aws_dynamodb_table.this.arn
}

output "gsi1_arn" {
  description = "ARN of the GSI1 index on the DynamoDB table"
  value       = "${aws_dynamodb_table.this.arn}/index/GSI1"
}
