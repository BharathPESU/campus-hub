variable "name" {
  type        = string
  description = "Name of the DynamoDB table"
}

variable "protect_data" {
  type        = bool
  description = "Enable deletion protection on the DynamoDB table"
  default     = false
}
