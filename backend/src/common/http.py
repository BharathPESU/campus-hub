"""HTTP helpers and Lambda decorator for API Gateway HTTP APIs."""

import base64
import functools
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.logging import correlation_paths

from common.errors import AppError, InternalError, ValidationError

logger = Logger()
tracer = Tracer()

ULID_REGEX = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


@dataclass
class DummyLambdaContext:
    function_name: str = "test-func"
    memory_limit_in_mb: int = 256
    invoked_function_arn: str = "arn:aws:lambda:ap-south-2:091319139186:function:test"
    aws_request_id: str = "test-request-id"


def _decimal_to_int_or_float(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def json_response(
    status: int, body: Any = None, headers: dict[str, str] | None = None
) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "cache-control": "no-store",
            **(headers or {}),
        },
        "body": "" if body is None else json.dumps(body, default=_decimal_to_int_or_float),
    }


def error_response(err: AppError, request_id: str) -> dict[str, Any]:
    return json_response(
        err.status,
        {
            "error": {"code": err.code, "message": err.message},
            "requestId": request_id,
        },
    )


def api_handler(fn: Callable) -> Callable:
    @logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
    @tracer.capture_lambda_handler
    def _inner(event: dict[str, Any], context: Any) -> dict[str, Any]:
        request_id = event.get("requestContext", {}).get("requestId", "unknown")
        try:
            return fn(event, context)
        except AppError as e:
            logger.warning("app_error", extra={"code": e.code, "status": e.status})
            return error_response(e, request_id)
        except Exception:
            logger.exception("unhandled")
            return error_response(InternalError("INTERNAL", "Something went wrong."), request_id)

    @functools.wraps(fn)
    def wrapper(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
        if context is None:
            context = DummyLambdaContext()
        return _inner(event, context)

    return wrapper


def parse_json_body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body")
    if raw is None or raw == "":
        raise ValidationError("INVALID_JSON", "Body must be valid JSON.")
    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except Exception:
            raise ValidationError("INVALID_JSON", "Base64 decoding failed.")
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        raise ValidationError("INVALID_JSON", "Body must be valid JSON.")
    if not isinstance(data, dict):
        raise ValidationError("INVALID_JSON", "Body must be a JSON object.")
    return data


def path_id(event: dict[str, Any]) -> str:
    v = (event.get("pathParameters") or {}).get("id", "")
    if not ULID_REGEX.fullmatch(v):
        raise ValidationError("INVALID_ID", "Invalid event id.")
    return v
