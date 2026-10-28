data "aws_caller_identity" "this" {}
data "aws_region" "this" {}
data "aws_partition" "this" {}

locals {
  prefix = "campus-hub-${var.env}"
}
