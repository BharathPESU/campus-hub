data "aws_caller_identity" "this" {}
data "aws_region" "this" {}
data "aws_partition" "this" {}

locals {
  prefix             = "campus-hub-${var.env}"
  domain_prefix      = "${local.prefix}-${substr(data.aws_caller_identity.this.account_id, -6, -1)}"
  scheduler_role_arn = "arn:${data.aws_partition.this.partition}:iam::${data.aws_caller_identity.this.account_id}:role/${local.prefix}-scheduler"
  schedule_group     = "${local.prefix}-reminders"
  reminders_dlq_arn  = "arn:${data.aws_partition.this.partition}:sqs:${data.aws_region.this.region}:${data.aws_caller_identity.this.account_id}:${local.prefix}-reminders-dlq"
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

module "messaging" {
  source       = "../../modules/messaging"
  prefix       = local.prefix
  sender_email = var.sender_email
}

module "workflows" {
  source         = "../../modules/workflows"
  prefix         = local.prefix
  table_name     = module.data.table_name
  table_arn      = module.data.table_arn
  topic_arn      = module.messaging.topic_arn
  schedule_group = local.schedule_group
}

module "functions" {
  source               = "../../modules/functions"
  prefix               = local.prefix
  table_name           = module.data.table_name
  table_arn            = module.data.table_arn
  gsi1_arn             = module.data.gsi1_arn
  topic_arn            = module.messaging.topic_arn
  queue_arn            = module.messaging.queue_arn
  ses_identity_arn     = module.messaging.ses_identity_arn
  state_machine_arn    = module.workflows.state_machine_arn
  schedule_group       = local.schedule_group
  scheduler_role_arn   = local.scheduler_role_arn
  reminders_dlq_arn    = local.reminders_dlq_arn
  email_mode           = var.email_mode
  sender_email         = var.sender_email
  reserved_concurrency = var.lambda_reserved_concurrency
}

module "scheduling" {
  source              = "../../modules/scheduling"
  prefix              = local.prefix
  reminder_lambda_arn = module.functions.function_arns["send_reminder"]
}

module "api" {
  source               = "../../modules/api"
  prefix               = local.prefix
  function_invoke_arns = module.functions.function_invoke_arns
  function_arns        = module.functions.function_arns
  issuer               = module.auth.issuer
  client_id            = module.auth.client_id
}
