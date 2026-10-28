"""Handler to register authenticated student for an event."""

import os
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

from common.auth import require_user
from common.db import cancellation_codes, get_event
from common.emails import confirmation_email
from common.errors import Conflict, NotFound, Unavailable
from common.http import api_handler, json_response, path_id
from common.keys import META_SK, event_pk, gsi_sk, user_gsi_pk, user_sk
from common.notify import publish_email
from common.timeutil import now_utc, to_iso

logger = Logger()


def _get_client():
    return boto3.client("dynamodb")


@api_handler
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Execute atomic registration transaction and publish confirmation email."""
    user = require_user(event, need_email=True)
    event_id = path_id(event)
    ev = get_event(event_id)
    if ev is None:
        raise NotFound("EVENT_NOT_FOUND", "Event not found.")

    now = to_iso(now_utc())
    if ev.get("status") != "ACTIVE":
        raise Conflict("EVENT_CANCELLED", "Event is cancelled.")
    if ev.get("startsAt", "") <= now:
        raise Conflict("EVENT_STARTED", "Event has already started.")
    if int(ev.get("seatsTaken", 0)) >= int(ev.get("capacity", 0)):
        raise Conflict("EVENT_FULL", "This event has no seats left.")

    table_name = os.environ.get("TABLE_NAME", "campus-hub-dev")
    client = _get_client()

    try:
        client.transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": table_name,
                        "Key": {
                            "PK": {"S": event_pk(event_id)},
                            "SK": {"S": META_SK},
                        },
                        "UpdateExpression": "SET #seats = #seats + :one",
                        "ConditionExpression": (
                            "#status = :active AND #seats < #cap AND #starts > :now"
                        ),
                        "ExpressionAttributeNames": {
                            "#seats": "seatsTaken",
                            "#cap": "capacity",
                            "#status": "status",
                            "#starts": "startsAt",
                        },
                        "ExpressionAttributeValues": {
                            ":one": {"N": "1"},
                            ":active": {"S": "ACTIVE"},
                            ":now": {"S": now},
                        },
                    }
                },
                {
                    "Put": {
                        "TableName": table_name,
                        "Item": {
                            "PK": {"S": event_pk(event_id)},
                            "SK": {"S": user_sk(user.sub)},
                            "entity": {"S": "REGISTRATION"},
                            "GSI1PK": {"S": user_gsi_pk(user.sub)},
                            "GSI1SK": {"S": gsi_sk(ev["startsAt"], event_id)},
                            "email": {"S": user.email or ""},
                            "eventTitle": {"S": ev.get("title", "")},
                            "startsAt": {"S": ev["startsAt"]},
                            "registeredAt": {"S": now},
                        },
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }
                },
            ]
        )
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code")
        if error_code == "TransactionCanceledException":
            reasons = cancellation_codes(e)
            # Index 1 = Put registration check
            if len(reasons) > 1 and reasons[1] == "ConditionalCheckFailed":
                raise Conflict("ALREADY_REGISTERED", "Already registered for this event.")
            # Index 0 = Update event seats check
            if reasons and reasons[0] == "ConditionalCheckFailed":
                cur = get_event(event_id)
                if cur is None:
                    raise NotFound("EVENT_NOT_FOUND", "Event not found.")
                if cur.get("status") != "ACTIVE":
                    raise Conflict("EVENT_CANCELLED", "Event is cancelled.")
                if cur.get("startsAt", "") <= now:
                    raise Conflict("EVENT_STARTED", "Event has already started.")
                raise Conflict("EVENT_FULL", "This event has no seats left.")
            if any(
                c in ("TransactionConflict", "ThrottlingError", "ProvisionedThroughputExceeded")
                for c in reasons
            ):
                raise Unavailable("TRY_AGAIN", "Service busy, please retry.")
        if error_code in ("ProvisionedThroughputExceededException", "ThrottlingException"):
            raise Unavailable("TRY_AGAIN", "Service busy, please retry.")
        raise

    # Attempt to send confirmation email asynchronously; failure must never fail registration
    if user.email:
        try:
            subject, body = confirmation_email(ev, user)
            publish_email("confirmation", user.email, subject, body)
        except Exception:
            logger.exception("confirmation_publish_failed")

    capacity = int(ev["capacity"])
    seats_taken = int(ev["seatsTaken"]) + 1
    return json_response(
        201,
        {
            "eventId": event_id,
            "registeredAt": now,
            "seatsLeft": max(capacity - seats_taken, 0),
        },
    )
