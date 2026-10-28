variable "prefix" {
  type        = string
  description = "Resource prefix, e.g. campus-hub-dev"
}

variable "reminder_lambda_arn" {
  type        = string
  description = "ARN of the send_reminder Lambda function"
}
