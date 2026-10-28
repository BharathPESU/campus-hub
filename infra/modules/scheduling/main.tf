data "aws_caller_identity" "this" {}

resource "aws_scheduler_schedule_group" "reminders" {
  name = "${var.prefix}-reminders"
}

resource "aws_sqs_queue" "dlq" {
  name                      = "${var.prefix}-reminders-dlq"
  sqs_managed_sse_enabled   = true
  message_retention_seconds = 1209600 # 14 days
}

resource "aws_iam_role" "scheduler" {
  name = "${var.prefix}-scheduler"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "scheduler.amazonaws.com"
        }
        Action = "sts:AssumeRole"
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = data.aws_caller_identity.this.account_id
          }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  name = "${var.prefix}-scheduler-policy"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "InvokeReminderLambda"
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = var.reminder_lambda_arn
      },
      {
        Sid      = "SendToDLQ"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.dlq.arn
      }
    ]
  })
}
