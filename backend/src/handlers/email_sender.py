"""SQS consumer handler to send emails via SESv2 or log them in dev mode."""

import json
import os
from dataclasses import dataclass
from typing import Any

import boto3
from aws_lambda_powertools import Logger

logger = Logger()


@dataclass
class DummyLambdaContext:
    function_name: str = "email_sender"
    memory_limit_in_mb: int = 256
    invoked_function_arn: str = "arn:aws:lambda:ap-south-2:091319139186:function:email_sender"
    aws_request_id: str = "email-sender-req-id"


def _get_ses_client():
    return boto3.client("sesv2")


def mask_email(email: str) -> str:
    """Mask email for safe logging (e.g. alice@example.com -> a***@example.com)."""
    if "@" in email:
        local, domain = email.split("@", 1)
        masked_local = f"{local[0]}***" if local else "***"
        return f"{masked_local}@{domain}"
    return "***"


@logger.inject_lambda_context
def _handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Process batch of email messages from SQS with ReportBatchItemFailures."""
    email_mode = os.environ.get("EMAIL_MODE", "log")
    sender_email = os.environ.get("SENDER_EMAIL", "")

    ses = _get_ses_client() if email_mode != "log" else None
    failures: list[dict[str, str]] = []

    records = event.get("Records", [])
    for rec in records:
        msg_id = rec.get("messageId", "unknown")
        try:
            raw_body = rec.get("body", "")
            data = json.loads(raw_body)
            # Handle potential SNS envelope if not raw delivery
            if isinstance(data, dict) and "Message" in data and "Type" in data:
                data = json.loads(data["Message"])

            if not isinstance(data, dict):
                raise ValueError("Message body must be a JSON object.")

            to_addr = data.get("to")
            subject = data.get("subject")
            body = data.get("body")

            if not (isinstance(to_addr, str) and to_addr.strip()):
                raise ValueError("Missing or invalid 'to' field.")
            if not (isinstance(subject, str) and subject.strip()):
                raise ValueError("Missing or invalid 'subject' field.")
            if not (isinstance(body, str) and body.strip()):
                raise ValueError("Missing or invalid 'body' field.")

            if email_mode == "log":
                logger.info(
                    "email_logged",
                    extra={"to": mask_email(to_addr), "subject": subject},
                )
            else:
                if not sender_email:
                    raise ValueError("SENDER_EMAIL must be set when EMAIL_MODE=ses")
                ses.send_email(
                    FromEmailAddress=sender_email,
                    Destination={"ToAddresses": [to_addr]},
                    Content={
                        "Simple": {
                            "Subject": {"Data": subject},
                            "Body": {"Text": {"Data": body}},
                        }
                    },
                )
        except Exception:
            logger.exception("send_email_failed", extra={"messageId": msg_id})
            failures.append({"itemIdentifier": msg_id})

    return {"batchItemFailures": failures}


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    if context is None:
        context = DummyLambdaContext()
    return _handler(event, context)
