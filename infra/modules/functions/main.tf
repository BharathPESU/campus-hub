data "aws_region" "this" {}
data "aws_caller_identity" "this" {}
data "aws_partition" "this" {}

data "archive_file" "package" {
  type        = "zip"
  source_dir  = "${path.module}/../../../build/lambda/package"
  output_path = "${path.module}/../../../build/lambda/package.zip"
}

locals {
  functions = {
    health = {
      description = "Health check endpoint"
    }
    list_events = {
      description = "List all active events"
    }
    get_event = {
      description = "Get details of an event"
    }
    create_event = {
      description = "Create a new event and schedule reminder"
    }
    start_cancel = {
      description = "Initiate cancellation workflow for an event"
    }
    register = {
      description = "Register authenticated user for an event"
    }
    unregister = {
      description = "Cancel registration for an event"
    }
    list_my_registrations = {
      description = "List all event registrations for authenticated user"
    }
    send_reminder = {
      description = "Dispatch event reminders via SNS"
    }
    email_sender = {
      description = "Process email outbox queue and dispatch emails"
    }
  }

  common_env = {
    POWERTOOLS_SERVICE_NAME = "campus-hub"
    POWERTOOLS_LOG_LEVEL    = "INFO"
  }

  function_env = {
    health = local.common_env

    list_events = merge(local.common_env, {
      TABLE_NAME = var.table_name
    })

    get_event = merge(local.common_env, {
      TABLE_NAME = var.table_name
    })

    create_event = merge(local.common_env, {
      TABLE_NAME          = var.table_name
      SCHEDULE_GROUP      = var.schedule_group
      SCHEDULER_ROLE_ARN  = var.scheduler_role_arn
      REMINDER_LAMBDA_ARN = "arn:${data.aws_partition.this.partition}:lambda:${data.aws_region.this.region}:${data.aws_caller_identity.this.account_id}:function:${var.prefix}-send_reminder"
      REMINDER_DLQ_ARN    = var.reminders_dlq_arn
      REMINDER_LEAD_HOURS = "24"
    })

    start_cancel = merge(local.common_env, {
      TABLE_NAME               = var.table_name
      CANCEL_STATE_MACHINE_ARN = var.state_machine_arn
    })

    register = merge(local.common_env, {
      TABLE_NAME = var.table_name
      TOPIC_ARN  = var.topic_arn
    })

    unregister = merge(local.common_env, {
      TABLE_NAME = var.table_name
    })

    list_my_registrations = merge(local.common_env, {
      TABLE_NAME = var.table_name
    })

    send_reminder = merge(local.common_env, {
      TABLE_NAME = var.table_name
      TOPIC_ARN  = var.topic_arn
    })

    email_sender = merge(local.common_env, {
      EMAIL_MODE   = var.email_mode
      SENDER_EMAIL = var.sender_email
    })
  }
}

resource "aws_cloudwatch_log_group" "lambda" {
  for_each = local.functions

  name              = "/aws/lambda/${var.prefix}-${each.key}"
  retention_in_days = 14
}

resource "aws_iam_role" "lambda" {
  for_each = local.functions

  name = "${var.prefix}-${each.key}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy" "lambda_base" {
  for_each = local.functions

  name = "${var.prefix}-${each.key}-base"
  role = aws_iam_role.lambda[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.lambda[each.key].arn}:*"
      },
      {
        Sid    = "XRayTracing"
        Effect = "Allow"
        Action = [
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy" "list_events" {
  name = "${var.prefix}-list_events-policy"
  role = aws_iam_role.lambda["list_events"].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "DynamoDBQueryGSI1"
        Effect   = "Allow"
        Action   = ["dynamodb:Query"]
        Resource = var.gsi1_arn
      }
    ]
  })
}

resource "aws_iam_role_policy" "get_event" {
  name = "${var.prefix}-get_event-policy"
  role = aws_iam_role.lambda["get_event"].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "DynamoDBGetItem"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem"]
        Resource = var.table_arn
      }
    ]
  })
}

resource "aws_iam_role_policy" "create_event" {
  name = "${var.prefix}-create_event-policy"
  role = aws_iam_role.lambda["create_event"].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBWrite"
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem"
        ]
        Resource = var.table_arn
      },
      {
        Sid      = "SchedulerCreateSchedule"
        Effect   = "Allow"
        Action   = ["scheduler:CreateSchedule"]
        Resource = "arn:${data.aws_partition.this.partition}:scheduler:${data.aws_region.this.region}:${data.aws_caller_identity.this.account_id}:schedule/${var.schedule_group}/reminder-*"
      },
      {
        Sid      = "IAMPassRoleToScheduler"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = var.scheduler_role_arn
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "scheduler.amazonaws.com"
          }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "start_cancel" {
  name = "${var.prefix}-start_cancel-policy"
  role = aws_iam_role.lambda["start_cancel"].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:UpdateItem"
        ]
        Resource = var.table_arn
      },
      {
        Sid      = "StatesStartExecution"
        Effect   = "Allow"
        Action   = ["states:StartExecution"]
        Resource = var.state_machine_arn
      }
    ]
  })
}

resource "aws_iam_role_policy" "register" {
  name = "${var.prefix}-register-policy"
  role = aws_iam_role.lambda["register"].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem"
        ]
        Resource = var.table_arn
      },
      {
        Sid      = "SNSPublish"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = var.topic_arn
      }
    ]
  })
}

resource "aws_iam_role_policy" "unregister" {
  name = "${var.prefix}-unregister-policy"
  role = aws_iam_role.lambda["unregister"].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:DeleteItem",
          "dynamodb:UpdateItem"
        ]
        Resource = var.table_arn
      }
    ]
  })
}

resource "aws_iam_role_policy" "list_my_registrations" {
  name = "${var.prefix}-list_my_registrations-policy"
  role = aws_iam_role.lambda["list_my_registrations"].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "DynamoDBQueryGSI1"
        Effect   = "Allow"
        Action   = ["dynamodb:Query"]
        Resource = var.gsi1_arn
      }
    ]
  })
}

resource "aws_iam_role_policy" "send_reminder" {
  name = "${var.prefix}-send_reminder-policy"
  role = aws_iam_role.lambda["send_reminder"].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
          "dynamodb:Query"
        ]
        Resource = var.table_arn
      },
      {
        Sid      = "SNSPublish"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = var.topic_arn
      }
    ]
  })
}

resource "aws_iam_role_policy" "email_sender" {
  name = "${var.prefix}-email_sender-policy"
  role = aws_iam_role.lambda["email_sender"].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SQSReceiveAndDelete"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes"
        ]
        Resource = var.queue_arn
      },
      {
        Sid      = "SESSendEmail"
        Effect   = "Allow"
        Action   = ["ses:SendEmail"]
        Resource = var.ses_identity_arn
      }
    ]
  })
}

resource "aws_lambda_function" "functions" {
  for_each = local.functions

  function_name                  = "${var.prefix}-${each.key}"
  description                    = each.value.description
  role                           = aws_iam_role.lambda[each.key].arn
  handler                        = "handlers.${each.key}.handler"
  runtime                        = "python3.12"
  architectures                  = ["arm64"]
  memory_size                    = 256
  timeout                        = 10
  reserved_concurrent_executions = var.reserved_concurrency

  filename         = data.archive_file.package.output_path
  source_code_hash = data.archive_file.package.output_base64sha256

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = local.function_env[each.key]
  }

  depends_on = [
    aws_cloudwatch_log_group.lambda,
    aws_iam_role_policy.lambda_base
  ]
}

resource "aws_lambda_event_source_mapping" "email_sender" {
  event_source_arn        = var.queue_arn
  function_name           = aws_lambda_function.functions["email_sender"].arn
  batch_size              = 10
  function_response_types = ["ReportBatchItemFailures"]
}
