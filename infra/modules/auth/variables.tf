variable "prefix" {
  type        = string
  description = "Resource prefix, e.g. campus-hub-dev"
}

variable "callback_urls" {
  type        = list(string)
  description = "Allowed callback URLs for OAuth client"
}

variable "logout_urls" {
  type        = list(string)
  description = "Allowed sign-out URLs for OAuth client"
}

variable "domain_prefix" {
  type        = string
  description = "Globally unique domain prefix for Cognito hosted UI"
}

variable "enable_test_auth" {
  type        = bool
  description = "Enable ALLOW_USER_PASSWORD_AUTH for CLI testing (temporary, removed in Phase 13)"
  default     = false
}
