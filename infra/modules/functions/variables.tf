variable "prefix" {
  type        = string
  description = "Resource prefix, e.g. campus-hub-dev"
}

variable "table_name" {
  type        = string
  description = "Name of the DynamoDB table"
}

variable "table_arn" {
  type        = string
  description = "ARN of the DynamoDB table"
}

variable "gsi1_arn" {
  type        = string
  description = "ARN of the DynamoDB GSI1 index"
}

variable "topic_arn" {
  type        = string
  description = "ARN of the event notifications SNS topic"
}

variable "queue_arn" {
  type        = string
  description = "ARN of the email outbox SQS queue"
}

variable "ses_identity_arn" {
  type        = string
  description = "ARN of the verified SES email identity"
}

variable "state_machine_arn" {
  type        = string
  description = "ARN of the Step Functions cancel state machine"
}

variable "schedule_group" {
  type        = string
  description = "Name of the EventBridge Scheduler schedule group"
}

variable "scheduler_role_arn" {
  type        = string
  description = "ARN of the EventBridge Scheduler IAM execution role"
}

variable "reminders_dlq_arn" {
  type        = string
  description = "ARN of the EventBridge Scheduler reminders DLQ"
}

variable "email_mode" {
  type        = string
  description = "Email dispatch mode: ses or log"
  default     = "log"
}

variable "sender_email" {
  type        = string
  description = "Verified sender email address for email dispatch"
}

variable "reserved_concurrency" {
  type        = number
  description = "Reserved concurrency for Lambda functions (null = unreserved)"
  default     = null
}
