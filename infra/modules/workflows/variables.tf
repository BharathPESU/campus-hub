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

variable "topic_arn" {
  type        = string
  description = "ARN of the event notifications SNS topic"
}

variable "schedule_group" {
  type        = string
  description = "Name of the EventBridge Scheduler schedule group"
}
