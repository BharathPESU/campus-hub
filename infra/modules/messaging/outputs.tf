output "topic_arn" {
  description = "ARN of the event notifications SNS topic"
  value       = aws_sns_topic.events.arn
}

output "topic_name" {
  description = "Name of the event notifications SNS topic"
  value       = aws_sns_topic.events.name
}

output "queue_arn" {
  description = "ARN of the email outbox SQS queue"
  value       = aws_sqs_queue.outbox.arn
}

output "queue_name" {
  description = "Name of the email outbox SQS queue"
  value       = aws_sqs_queue.outbox.name
}

output "dlq_arn" {
  description = "ARN of the email outbox DLQ"
  value       = aws_sqs_queue.dlq.arn
}

output "dlq_name" {
  description = "Name of the email outbox DLQ"
  value       = aws_sqs_queue.dlq.name
}

output "ses_identity_arn" {
  description = "ARN of the SES email identity"
  value       = aws_ses_email_identity.sender.arn
}
