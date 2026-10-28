output "group_name" {
  description = "Name of the EventBridge Scheduler schedule group"
  value       = aws_scheduler_schedule_group.reminders.name
}

output "dlq_arn" {
  description = "ARN of the EventBridge Scheduler reminders DLQ"
  value       = aws_sqs_queue.dlq.arn
}

output "dlq_name" {
  description = "Name of the EventBridge Scheduler reminders DLQ"
  value       = aws_sqs_queue.dlq.name
}

output "role_arn" {
  description = "ARN of the EventBridge Scheduler IAM execution role"
  value       = aws_iam_role.scheduler.arn
}
