"""Admin handler to create an event and schedule its reminder."""

import json
import os
from datetime import timedelta
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from ulid import ULID

from common.auth import require_admin
from common.db import to_public_event
from common.errors import InternalError
from common.http import api_handler, json_response, parse_json_body
from common.keys import EVENTS_GSI_PK, META_SK, event_pk, gsi_sk
from common.timeutil import now_utc, parse_iso, to_iso
from common.validation import validate_create_event

logger = Logger()


def _get_table():
    return boto3.resource("dynamodb").Table(os.environ.get("TABLE_NAME", "campus-hub-dev"))


def _get_scheduler_client():
    return boto3.client("scheduler")


@api_handler
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Validate, create event in DynamoDB, and register one-time reminder schedule."""
    admin = require_admin(event)
    body = parse_json_body(event)
    data = validate_create_event(body)

    table = _get_table()
    now = now_utc()
    event_id = str(ULID())
    starts = parse_iso(data["startsAt"])

    lead_hours = int(os.environ.get("REMINDER_LEAD_HOURS", "24"))
    reminder_at = starts - timedelta(hours=lead_hours)

    item: dict[str, Any] = {
        "PK": event_pk(event_id),
        "SK": META_SK,
        "entity": "EVENT",
        "GSI1PK": EVENTS_GSI_PK,
        "GSI1SK": gsi_sk(data["startsAt"], event_id),
        "title": data["title"],
        "description": data.get("description", ""),
        "location": data["location"],
        "startsAt": data["startsAt"],
        "capacity": data["capacity"],
        "seatsTaken": 0,
        "status": "ACTIVE",
        "reminderStatus": "PENDING",
        "createdBy": admin.sub,
        "createdAt": to_iso(now),
    }

    table.put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")

    schedule_group = os.environ.get("SCHEDULE_GROUP", "")
    scheduler_role_arn = os.environ.get("SCHEDULER_ROLE_ARN", "")
    reminder_lambda_arn = os.environ.get("REMINDER_LAMBDA_ARN", "")
    reminder_dlq_arn = os.environ.get("REMINDER_DLQ_ARN", "")

    # Only schedule if reminder time is more than 60 seconds into the future
    if reminder_at > now + timedelta(seconds=60) and schedule_group:
        sched_name = f"reminder-{event_id}"
        scheduler = _get_scheduler_client()
        try:
            scheduler.create_schedule(
                Name=sched_name,
                GroupName=schedule_group,
                ScheduleExpression=f"at({reminder_at.strftime('%Y-%m-%dT%H:%M:%S')})",
                ScheduleExpressionTimezone="UTC",
                FlexibleTimeWindow={"Mode": "OFF"},
                ActionAfterCompletion="DELETE",
                Target={
                    "Arn": reminder_lambda_arn,
                    "RoleArn": scheduler_role_arn,
                    "Input": json.dumps({"eventId": event_id}),
                    "RetryPolicy": {
                        "MaximumRetryAttempts": 3,
                        "MaximumEventAgeInSeconds": 3600,
                    },
                    "DeadLetterConfig": {"Arn": reminder_dlq_arn},
                },
            )
            table.update_item(
                Key={"PK": event_pk(event_id), "SK": META_SK},
                UpdateExpression="SET #rs = :sched, reminderScheduleName = :sname",
                ExpressionAttributeNames={"#rs": "reminderStatus"},
                ExpressionAttributeValues={":sched": "SCHEDULED", ":sname": sched_name},
            )
            item["reminderStatus"] = "SCHEDULED"
            item["reminderScheduleName"] = sched_name
        except Exception:
            logger.exception("schedule_failed")
            # Compensation: delete the created event if schedule fails
            try:
                table.delete_item(Key={"PK": event_pk(event_id), "SK": META_SK})
            except Exception:
                logger.exception("compensation_delete_failed")
            raise InternalError("INTERNAL", "Failed to schedule event reminder.")
    else:
        table.update_item(
            Key={"PK": event_pk(event_id), "SK": META_SK},
            UpdateExpression="SET #rs = :skipped",
            ExpressionAttributeNames={"#rs": "reminderStatus"},
            ExpressionAttributeValues={":skipped": "SKIPPED"},
        )
        item["reminderStatus"] = "SKIPPED"

    return json_response(201, to_public_event(item))
