variable "env" {
  type        = string
  description = "Deployment environment name"
  default     = "dev"
}

variable "region" {
  type        = string
  description = "AWS Region for deployment"
  default     = "ap-south-2"
}

variable "sender_email" {
  type        = string
  description = "Sender email address for notifications"
}

variable "admin_email" {
  type        = string
  description = "Admin email address for ops alerts and budget notifications"
}

variable "email_mode" {
  type        = string
  description = "Email dispatch mode: ses or log"
  default     = "log"

  validation {
    condition     = contains(["ses", "log"], var.email_mode)
    error_message = "email_mode must be either 'ses' or 'log'"
  }
}

variable "protect_data" {
  type        = bool
  description = "Enable deletion protection on stateful data resources"
  default     = false
}

variable "force_destroy" {
  type        = bool
  description = "Allow destroying S3 buckets containing objects"
  default     = true
}

variable "budget_limit_usd" {
  type        = number
  description = "Monthly budget limit in USD"
  default     = 1
}

variable "lambda_reserved_concurrency" {
  type        = number
  description = "Reserved concurrent executions for Lambda functions (null = unreserved)"
  default     = null
}
