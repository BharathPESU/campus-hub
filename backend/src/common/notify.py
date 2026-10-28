"""SNS notification publishing for event emails."""

import json
import os
from typing import Any

import boto3

TOPIC_ARN = os.environ.get("TOPIC_ARN", "")


def _get_sns_client():
    return boto3.client("sns")


def publish_email(
    kind: str, to: str, subject: str, body: str, topic_arn: str | None = None
) -> None:
    """Publish a single email notification message to the SNS topic."""
    arn = topic_arn or os.environ.get("TOPIC_ARN", TOPIC_ARN)
    if not arn:
        raise ValueError("TOPIC_ARN must be set to publish emails.")

    sns = _get_sns_client()
    payload = json.dumps({"to": to, "subject": subject, "body": body})
    sns.publish(
        TopicArn=arn,
        Message=payload,
        MessageAttributes={
            "type": {
                "DataType": "String",
                "StringValue": kind,
            }
        },
    )


def publish_email_batch(
    kind: str, messages: list[dict[str, Any]], topic_arn: str | None = None
) -> None:
    """Publish a batch of email messages to SNS in chunks of up to 10."""
    arn = topic_arn or os.environ.get("TOPIC_ARN", TOPIC_ARN)
    if not arn:
        raise ValueError("TOPIC_ARN must be set to publish emails.")

    sns = _get_sns_client()
    # SNS publish_batch allows a maximum of 10 entries per batch call
    chunk_size = 10
    for i in range(0, len(messages), chunk_size):
        chunk = messages[i : i + chunk_size]
        entries = [
            {
                "Id": str(idx),
                "Message": json.dumps(m),
                "MessageAttributes": {
                    "type": {
                        "DataType": "String",
                        "StringValue": kind,
                    }
                },
            }
            for idx, m in enumerate(chunk)
        ]
        resp = sns.publish_batch(TopicArn=arn, PublishBatchRequestEntries=entries)
        failed = resp.get("Failed", [])
        if failed:
            raise RuntimeError(f"{len(failed)} SNS publishes failed in batch: {failed}")
