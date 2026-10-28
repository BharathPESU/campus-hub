"""Public handler to list upcoming active events."""

import os
from typing import Any

import boto3

from common.db import to_public_event
from common.http import api_handler, json_response
from common.keys import EVENTS_GSI_PK, GSI1
from common.timeutil import now_utc, to_iso


def _get_table():
    return boto3.resource("dynamodb").Table(os.environ.get("TABLE_NAME", "campus-hub-dev"))


@api_handler
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Query upcoming events from GSI1 filtered by active status."""
    table = _get_table()
    now = to_iso(now_utc())

    items: list[dict[str, Any]] = []
    start_key: dict[str, Any] | None = None
    pages = 0

    while pages < 5 and len(items) < 50:
        query_kwargs: dict[str, Any] = {
            "IndexName": GSI1,
            "KeyConditionExpression": "#p = :p AND #s >= :now",
            "FilterExpression": "#status = :active",
            "ExpressionAttributeNames": {
                "#p": "GSI1PK",
                "#s": "GSI1SK",
                "#status": "status",
            },
            "ExpressionAttributeValues": {
                ":p": EVENTS_GSI_PK,
                ":now": now,
                ":active": "ACTIVE",
            },
            "ScanIndexForward": True,
        }
        if start_key:
            query_kwargs["ExclusiveStartKey"] = start_key

        resp = table.query(**query_kwargs)
        items.extend(resp.get("Items", []))
        start_key = resp.get("LastEvaluatedKey")
        pages += 1
        if not start_key:
            break

    return json_response(200, {"events": [to_public_event(i) for i in items[:50]]})
