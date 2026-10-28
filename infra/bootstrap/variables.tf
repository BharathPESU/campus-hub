variable "account_id" {
  type        = string
  description = "AWS Account ID used for unique naming of the state bucket"
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be a 12-digit AWS account ID"
  }
}

variable "region" {
  type        = string
  description = "AWS Region for the Terraform state bucket"
  default     = "ap-south-2"
}
