resource "aws_dynamodb_table" "this" {
  name           = var.name
  billing_mode   = "PROVISIONED"
  read_capacity  = 5
  write_capacity = 5
  hash_key       = "PK"
  range_key      = "SK"
  stream_enabled = false

  deletion_protection_enabled = var.protect_data

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  attribute {
    name = "GSI1PK"
    type = "S"
  }

  attribute {
    name = "GSI1SK"
    type = "S"
  }

  global_secondary_index {
    name            = "GSI1"
    hash_key        = "GSI1PK"
    range_key       = "GSI1SK"
    write_capacity  = 5
    read_capacity   = 5
    projection_type = "ALL"
  }
}
