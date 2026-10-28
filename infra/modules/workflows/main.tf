data "aws_region" "this" {}
data "aws_caller_identity" "this" {}
data "aws_partition" "this" {}

resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/aws/vendedlogs/states/${var.prefix}-cancel-event"
  retention_in_days = 14
}

resource "aws_iam_role" "sfn" {
  name = "${var.prefix}-cancel-workflow"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "states.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy" "sfn" {
  name = "${var.prefix}-cancel-workflow-policy"
  role = aws_iam_role.sfn.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CloudWatchLogsDelivery"
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups"
        ]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchLogsEvents"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.sfn.arn}:*"
      },
      {
        Sid    = "XRayTracing"
        Effect = "Allow"
        Action = [
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords",
          "xray:GetSamplingRules",
          "xray:GetSamplingTargets"
        ]
        Resource = "*"
      },
      {
        Sid    = "DynamoDBAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:Query",
          "dynamodb:UpdateItem"
        ]
        Resource = var.table_arn
      },
      {
        Sid      = "SNSPublish"
        Effect   = "Allow"
        Action   = "sns:Publish"
        Resource = var.topic_arn
      },
      {
        Sid      = "SchedulerDeleteSchedule"
        Effect   = "Allow"
        Action   = "scheduler:DeleteSchedule"
        Resource = "arn:${data.aws_partition.this.partition}:scheduler:${data.aws_region.this.region}:${data.aws_caller_identity.this.account_id}:schedule/${var.schedule_group}/*"
      }
    ]
  })
}

resource "aws_sfn_state_machine" "cancel" {
  name     = "${var.prefix}-cancel-event"
  type     = "STANDARD"
  role_arn = aws_iam_role.sfn.arn

  definition = templatefile("${path.module}/cancel.asl.json.tftpl", {
    table_name     = var.table_name
    topic_arn      = var.topic_arn
    schedule_group = var.schedule_group
  })

  logging_configuration {
    level                  = "ERROR"
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = false
  }

  tracing_configuration {
    enabled = true
  }

  depends_on = [
    aws_iam_role_policy.sfn,
    aws_cloudwatch_log_group.sfn
  ]
}
