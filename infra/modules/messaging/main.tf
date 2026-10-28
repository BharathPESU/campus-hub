resource "aws_sns_topic" "events" {
  name              = "${var.prefix}-event-notifications"
  kms_master_key_id = "alias/aws/sns"
}

resource "aws_sqs_queue" "dlq" {
  name                      = "${var.prefix}-email-outbox-dlq"
  sqs_managed_sse_enabled   = true
  message_retention_seconds = 1209600 # 14 days
}

resource "aws_sqs_queue" "outbox" {
  name                       = "${var.prefix}-email-outbox"
  sqs_managed_sse_enabled    = true
  visibility_timeout_seconds = 60

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue_policy" "outbox" {
  queue_url = aws_sqs_queue.outbox.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowSNSPublish"
        Effect = "Allow"
        Principal = {
          Service = "sns.amazonaws.com"
        }
        Action   = "sqs:SendMessage"
        Resource = aws_sqs_queue.outbox.arn
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = aws_sns_topic.events.arn
          }
        }
      }
    ]
  })
}

resource "aws_sns_topic_subscription" "events_to_outbox" {
  topic_arn            = aws_sns_topic.events.arn
  protocol             = "sqs"
  endpoint             = aws_sqs_queue.outbox.arn
  raw_message_delivery = true
}

resource "aws_ses_email_identity" "sender" {
  email = var.sender_email
}
