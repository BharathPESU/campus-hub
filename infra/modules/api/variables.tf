variable "prefix" {
  type        = string
  description = "Resource prefix, e.g. campus-hub-dev"
}

variable "function_invoke_arns" {
  type        = map(string)
  description = "Map of Lambda function names to invoke ARNs"
}

variable "function_arns" {
  type        = map(string)
  description = "Map of Lambda function names to function ARNs"
}

variable "issuer" {
  type        = string
  description = "Cognito User Pool OIDC issuer URL for JWT authorizer"
}

variable "client_id" {
  type        = string
  description = "Cognito User Pool Client ID for JWT authorizer audience"
}
