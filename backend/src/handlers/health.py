"""Public health check handler."""

from typing import Any

from common.http import api_handler, json_response
from common.timeutil import now_utc, to_iso


@api_handler
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Return health status and current UTC time."""
    return json_response(200, {"status": "ok", "time": to_iso(now_utc())})
