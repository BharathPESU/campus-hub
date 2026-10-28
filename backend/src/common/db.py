"""DynamoDB helper client and item mapper."""

import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

from common.keys import META_SK, event_pk

TABLE_NAME = os.environ.get("TABLE_NAME", "campus-hub-dev")

_resource = boto3.resource("dynamodb")
table = _resource.Table(TABLE_NAME)
client = _resource.meta.client


def get_event(event_id: str) -> dict[str, Any] | None:
    """Fetch raw event item from DynamoDB table."""
    current_table = boto3.resource("dynamodb").Table(os.environ.get("TABLE_NAME", "campus-hub-dev"))
    resp = current_table.get_item(Key={"PK": event_pk(event_id), "SK": META_SK})
    return resp.get("Item")


def to_public_event(it: dict[str, Any]) -> dict[str, Any]:
    """Map internal DynamoDB item to public API event shape."""
    pk = it["PK"]
    event_id = pk.removeprefix("EVENT#") if isinstance(pk, str) else str(pk)
    capacity = int(it["capacity"])
    seats_taken = int(it["seatsTaken"])
    return {
        "eventId": event_id,
        "title": it["title"],
        "description": it.get("description", ""),
        "location": it["location"],
        "startsAt": it["startsAt"],
        "capacity": capacity,
        "seatsTaken": seats_taken,
        "seatsLeft": max(capacity - seats_taken, 0),
        "status": it["status"],
    }


def cancellation_codes(client_error: ClientError) -> list[str | None]:
    """Extract cancellation reason codes from a TransactionCanceledException."""
    reasons = client_error.response.get("CancellationReasons", [])
    return [r.get("Code") for r in reasons]
