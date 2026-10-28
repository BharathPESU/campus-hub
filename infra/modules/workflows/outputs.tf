output "state_machine_arn" {
  description = "ARN of the event cancellation Step Functions state machine"
  value       = aws_sfn_state_machine.cancel.arn
}

output "state_machine_name" {
  description = "Name of the event cancellation Step Functions state machine"
  value       = aws_sfn_state_machine.cancel.name
}
