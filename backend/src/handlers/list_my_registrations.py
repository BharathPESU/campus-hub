"""Handler to list all registrations belonging to the authenticated student."""

import os
from typing import Any

import boto3

from common.auth import require_user
from common.http import api_handler, json_response
from common.keys import GSI1, user_gsi_pk


def _get_table():
    return boto3.resource("dynamodb").Table(os.environ.get("TABLE_NAME", "campus-hub-dev"))


@api_handler
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Query user registrations from GSI1."""
    user = require_user(event)
    table = _get_table()

    resp = table.query(
        IndexName=GSI1,
        KeyConditionExpression="#p = :p",
        ExpressionAttributeNames={"#p": "GSI1PK"},
        ExpressionAttributeValues={":p": user_gsi_pk(user.sub)},
        ScanIndexForward=True,
    )

    items = resp.get("Items", [])
    registrations = [
        {
            "eventId": it["PK"].removeprefix("EVENT#")
            if isinstance(it["PK"], str)
            else str(it["PK"]),
            "eventTitle": it.get("eventTitle", ""),
            "startsAt": it.get("startsAt", ""),
            "registeredAt": it.get("registeredAt", ""),
        }
        for it in items
    ]

    return json_response(200, {"registrations": registrations})
