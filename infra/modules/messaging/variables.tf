variable "prefix" {
  type        = string
  description = "Resource prefix, e.g. campus-hub-dev"
}

variable "sender_email" {
  type        = string
  description = "Sender email address for SES identity verification"
}
