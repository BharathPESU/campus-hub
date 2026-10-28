data "aws_caller_identity" "this" {}
data "aws_region" "this" {}
data "aws_partition" "this" {}

locals {
  prefix        = "campus-hub-${var.env}"
  domain_prefix = "${local.prefix}-${substr(data.aws_caller_identity.this.account_id, -6, -1)}"
}

module "data" {
  source       = "../../modules/data"
  name         = local.prefix
  protect_data = var.protect_data
}

module "auth" {
  source           = "../../modules/auth"
  prefix           = local.prefix
  domain_prefix    = local.domain_prefix
  callback_urls    = ["http://localhost:5173/"]
  logout_urls      = ["http://localhost:5173/"]
  enable_test_auth = true
}
