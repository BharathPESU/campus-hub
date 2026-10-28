"""Admin handler to initiate event cancellation workflow."""

import json
import os
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

from common.auth import require_admin
from common.db import get_event
from common.errors import InternalError, NotFound
from common.http import api_handler, json_response, path_id
from common.keys import META_SK, event_pk

logger = Logger()


def _get_table():
    return boto3.resource("dynamodb").Table(os.environ.get("TABLE_NAME", "campus-hub-dev"))


def _get_sfn_client():
    return boto3.client("stepfunctions")


@api_handler
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Switch event status to CANCELLING and trigger Step Functions cancel workflow."""
    require_admin(event)
    event_id = path_id(event)
    table = _get_table()
    sfn_arn = os.environ.get("CANCEL_STATE_MACHINE_ARN", "")

    try:
        table.update_item(
            Key={"PK": event_pk(event_id), "SK": META_SK},
            UpdateExpression="SET #status = :cancelling",
            ConditionExpression="attribute_exists(PK) AND #status = :active",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":cancelling": "CANCELLING", ":active": "ACTIVE"},
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            it = get_event(event_id)
            if it is None:
                raise NotFound("EVENT_NOT_FOUND", "Event not found.")
            # Already CANCELLING or CANCELLED: idempotent return 202
            return json_response(202, {"eventId": event_id, "status": it["status"]})
        raise

    ev = get_event(event_id)
    if not ev:
        raise NotFound("EVENT_NOT_FOUND", "Event not found.")

    if sfn_arn:
        sfn = _get_sfn_client()
        execution_name = f"cancel-{event_id}"
        payload = json.dumps(
            {"eventId": event_id, "title": ev.get("title", ""), "startsAt": ev.get("startsAt", "")}
        )
        try:
            sfn.start_execution(
                stateMachineArn=sfn_arn,
                name=execution_name,
                input=payload,
            )
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "ExecutionAlreadyExists":
                pass  # Idempotent execution
            else:
                logger.exception("sfn_start_execution_failed")
                # Compensation: revert status to ACTIVE
                try:
                    table.update_item(
                        Key={"PK": event_pk(event_id), "SK": META_SK},
                        UpdateExpression="SET #status = :active",
                        ConditionExpression="#status = :cancelling",
                        ExpressionAttributeNames={"#status": "status"},
                        ExpressionAttributeValues={
                            ":active": "ACTIVE",
                            ":cancelling": "CANCELLING",
                        },
                    )
                except Exception:
                    logger.exception("compensation_revert_failed")
                raise InternalError("INTERNAL", "Failed to start cancellation workflow.")
        except Exception:
            logger.exception("sfn_start_execution_failed")
            try:
                table.update_item(
                    Key={"PK": event_pk(event_id), "SK": META_SK},
                    UpdateExpression="SET #status = :active",
                    ConditionExpression="#status = :cancelling",
                    ExpressionAttributeNames={"#status": "status"},
                    ExpressionAttributeValues={
                        ":active": "ACTIVE",
                        ":cancelling": "CANCELLING",
                    },
                )
            except Exception:
                logger.exception("compensation_revert_failed")
            raise InternalError("INTERNAL", "Failed to start cancellation workflow.")

    return json_response(202, {"eventId": event_id, "status": "CANCELLING"})
