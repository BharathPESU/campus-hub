"""Public handler to retrieve a single event by ID."""

from typing import Any

from common.db import get_event, to_public_event
from common.errors import NotFound
from common.http import api_handler, json_response, path_id


@api_handler
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Retrieve event by ID from DynamoDB table."""
    event_id = path_id(event)
    item = get_event(event_id)
    if item is None:
        raise NotFound("EVENT_NOT_FOUND", "Event not found.")

    return json_response(200, to_public_event(item))
