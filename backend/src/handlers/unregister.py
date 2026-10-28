"""Handler to cancel current user's registration for an event."""

import os
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

from common.auth import require_user
from common.db import cancellation_codes
from common.errors import InternalError, NotFound, Unavailable
from common.http import api_handler, json_response, path_id
from common.keys import META_SK, event_pk, user_sk

logger = Logger()


def _get_client():
    return boto3.client("dynamodb")


@api_handler
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Atomically delete registration item and decrement seatsTaken counter."""
    user = require_user(event)
    event_id = path_id(event)
    table_name = os.environ.get("TABLE_NAME", "campus-hub-dev")
    client = _get_client()

    try:
        client.transact_write_items(
            TransactItems=[
                {
                    "Delete": {
                        "TableName": table_name,
                        "Key": {
                            "PK": {"S": event_pk(event_id)},
                            "SK": {"S": user_sk(user.sub)},
                        },
                        "ConditionExpression": "attribute_exists(PK)",
                    }
                },
                {
                    "Update": {
                        "TableName": table_name,
                        "Key": {
                            "PK": {"S": event_pk(event_id)},
                            "SK": {"S": META_SK},
                        },
                        "UpdateExpression": "SET #seats = #seats - :one",
                        "ConditionExpression": "#seats > :zero",
                        "ExpressionAttributeNames": {"#seats": "seatsTaken"},
                        "ExpressionAttributeValues": {":one": {"N": "1"}, ":zero": {"N": "0"}},
                    }
                },
            ]
        )
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code")
        if error_code == "TransactionCanceledException":
            reasons = cancellation_codes(e)
            if reasons and reasons[0] == "ConditionalCheckFailed":
                raise NotFound("NOT_REGISTERED", "Not registered for this event.")
            if len(reasons) > 1 and reasons[1] == "ConditionalCheckFailed":
                logger.error("seat_counter_inconsistent")
                raise InternalError("INTERNAL", "Seat counter inconsistent.")
            if any(
                c in ("TransactionConflict", "ThrottlingError", "ProvisionedThroughputExceeded")
                for c in reasons
            ):
                raise Unavailable("TRY_AGAIN", "Service busy, please retry.")
        if error_code in ("ProvisionedThroughputExceededException", "ThrottlingException"):
            raise Unavailable("TRY_AGAIN", "Service busy, please retry.")
        raise

    return json_response(204)
