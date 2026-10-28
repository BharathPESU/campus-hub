"""Scheduler-invoked handler to send reminder emails to event registrants."""

import os
from dataclasses import dataclass
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

from common.db import get_event
from common.emails import reminder_email
from common.keys import META_SK, event_pk
from common.notify import publish_email_batch
from common.timeutil import now_utc, to_iso

logger = Logger()


@dataclass
class DummyLambdaContext:
    function_name: str = "send_reminder"
    memory_limit_in_mb: int = 256
    invoked_function_arn: str = "arn:aws:lambda:ap-south-2:091319139186:function:send_reminder"
    aws_request_id: str = "reminder-req-id"


def _get_table():
    return boto3.resource("dynamodb").Table(os.environ.get("TABLE_NAME", "campus-hub-dev"))


@logger.inject_lambda_context
def _handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Claim reminder, query registrants, and publish reminder email batch."""
    event_id = event.get("eventId")
    if not event_id:
        logger.error("missing_event_id", extra={"event": event})
        return {"skipped": "missing_event_id"}

    ev = get_event(event_id)
    if ev is None or ev.get("status") != "ACTIVE":
        logger.info("reminder_skipped_not_active", extra={"eventId": event_id})
        return {"skipped": "not_active"}

    table = _get_table()
    now = to_iso(now_utc())

    # Atomic claim: prevent duplicate execution in at-least-once delivery
    try:
        table.update_item(
            Key={"PK": event_pk(event_id), "SK": META_SK},
            UpdateExpression="SET #rs = :sending, reminderSentAt = :now",
            ConditionExpression="attribute_not_exists(reminderSentAt) AND #status = :active",
            ExpressionAttributeNames={"#rs": "reminderStatus", "#status": "status"},
            ExpressionAttributeValues={":sending": "SENDING", ":now": now, ":active": "ACTIVE"},
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            logger.info("reminder_already_claimed", extra={"eventId": event_id})
            return {"skipped": "already_claimed"}
        raise

    try:
        # Query all registrants for the event (pagination support)
        registrants: list[dict[str, Any]] = []
        last_key = None
        while True:
            query_kwargs: dict[str, Any] = {
                "KeyConditionExpression": "#pk = :pk AND begins_with(#sk, :prefix)",
                "ExpressionAttributeNames": {"#pk": "PK", "#sk": "SK"},
                "ExpressionAttributeValues": {
                    ":pk": event_pk(event_id),
                    ":prefix": "USER#",
                },
            }
            if last_key:
                query_kwargs["ExclusiveStartKey"] = last_key
            resp = table.query(**query_kwargs)
            registrants.extend(resp.get("Items", []))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break

        messages: list[dict[str, Any]] = []
        for reg in registrants:
            email = reg.get("email")
            if email:
                subject, body = reminder_email(ev, reg)
                messages.append({"to": email, "subject": subject, "body": body})

        if messages:
            publish_email_batch("reminder", messages)

        table.update_item(
            Key={"PK": event_pk(event_id), "SK": META_SK},
            UpdateExpression="SET #rs = :sent",
            ExpressionAttributeNames={"#rs": "reminderStatus"},
            ExpressionAttributeValues={":sent": "SENT"},
        )
        return {"sent": len(messages)}

    except Exception:
        logger.exception("reminder_publish_failed", extra={"eventId": event_id})
        # Release the claim so EventBridge Scheduler can retry
        try:
            table.update_item(
                Key={"PK": event_pk(event_id), "SK": META_SK},
                UpdateExpression="REMOVE reminderSentAt SET #rs = :scheduled",
                ExpressionAttributeNames={"#rs": "reminderStatus"},
                ExpressionAttributeValues={":scheduled": "SCHEDULED"},
            )
        except Exception:
            logger.exception("release_claim_failed", extra={"eventId": event_id})
        raise


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    if context is None:
        context = DummyLambdaContext()
    return _handler(event, context)
