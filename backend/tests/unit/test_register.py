"""Unit tests for event registration and unregistration flows."""

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws
from ulid import ULID

from common.keys import GSI1, META_SK, event_pk, user_sk
from handlers.list_my_registrations import handler as list_my_registrations_handler
from handlers.register import handler as register_handler
from handlers.unregister import handler as unregister_handler

TABLE_NAME = "campus-hub-test"


@pytest.fixture(autouse=True)
def setup_env():
    os.environ["TABLE_NAME"] = TABLE_NAME
    os.environ["TOPIC_ARN"] = "arn:aws:sns:ap-south-2:123456789012:test-topic"
    os.environ["AWS_DEFAULT_REGION"] = "ap-south-2"


@pytest.fixture
def dynamodb_table():
    with mock_aws():
        sns = boto3.client("sns", region_name="ap-south-2")
        topic = sns.create_topic(Name="test-topic")
        os.environ["TOPIC_ARN"] = topic["TopicArn"]

        client = boto3.client("dynamodb", region_name="ap-south-2")
        client.create_table(
            TableName=TABLE_NAME,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "GSI1PK", "AttributeType": "S"},
                {"AttributeName": "GSI1SK", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": GSI1,
                    "KeySchema": [
                        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield client


def make_auth_event(
    user_id: str = "user-1",
    email: str | None = "user1@example.com",
    groups: str = "[student]",
    token_use: str = "id",
    path_parameters: dict | None = None,
) -> dict:
    claims: dict = {
        "sub": user_id,
        "token_use": token_use,
        "cognito:groups": groups,
    }
    if email is not None:
        claims["email"] = email

    return {
        "requestContext": {
            "requestId": "req-123",
            "authorizer": {
                "jwt": {
                    "claims": claims,
                }
            },
        },
        "pathParameters": path_parameters or {},
        "body": None,
    }


def seed_event(
    dynamodb_table,
    event_id: str,
    capacity: int = 10,
    seats_taken: int = 0,
    status: str = "ACTIVE",
    hours_from_now: int = 48,
):
    starts_at = (datetime.now(timezone.utc) + timedelta(hours=hours_from_now)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    dynamodb_table.put_item(
        TableName=TABLE_NAME,
        Item={
            "PK": {"S": event_pk(event_id)},
            "SK": {"S": META_SK},
            "entity": {"S": "EVENT"},
            "title": {"S": f"Event {event_id}"},
            "capacity": {"N": str(capacity)},
            "seatsTaken": {"N": str(seats_taken)},
            "status": {"S": status},
            "startsAt": {"S": starts_at},
        },
    )
    return starts_at


def test_register_happy_path(dynamodb_table):
    event_id = str(ULID())
    seed_event(dynamodb_table, event_id, capacity=5, seats_taken=0)

    event = make_auth_event(
        user_id="user-1",
        email="student1@campus.edu",
        path_parameters={"id": event_id},
    )

    with patch("handlers.register.publish_email") as mock_pub:
        resp = register_handler(event, None)

    assert resp["statusCode"] == 201
    body = json.loads(resp["body"])
    assert body["eventId"] == event_id
    assert body["seatsLeft"] == 4
    mock_pub.assert_called_once()

    # Verify table state
    table = boto3.resource("dynamodb", region_name="ap-south-2").Table(TABLE_NAME)
    ev_item = table.get_item(Key={"PK": event_pk(event_id), "SK": META_SK})["Item"]
    assert int(ev_item["seatsTaken"]) == 1

    reg_item = table.get_item(Key={"PK": event_pk(event_id), "SK": user_sk("user-1")})["Item"]
    assert reg_item["email"] == "student1@campus.edu"


def test_register_same_user_twice_fails(dynamodb_table):
    event_id = str(ULID())
    seed_event(dynamodb_table, event_id, capacity=5, seats_taken=0)

    event = make_auth_event(
        user_id="user-1",
        email="student1@campus.edu",
        path_parameters={"id": event_id},
    )

    with patch("common.notify.publish_email"):
        res1 = register_handler(event, None)
        assert res1["statusCode"] == 201

        res2 = register_handler(event, None)
        assert res2["statusCode"] == 409
        body = json.loads(res2["body"])
        assert body["error"]["code"] == "ALREADY_REGISTERED"

    # seatsTaken should remain 1
    table = boto3.resource("dynamodb", region_name="ap-south-2").Table(TABLE_NAME)
    ev_item = table.get_item(Key={"PK": event_pk(event_id), "SK": META_SK})["Item"]
    assert int(ev_item["seatsTaken"]) == 1


def test_register_event_full_fails(dynamodb_table):
    event_id = str(ULID())
    seed_event(dynamodb_table, event_id, capacity=1, seats_taken=0)

    event1 = make_auth_event(
        user_id="user-1",
        email="user1@campus.edu",
        path_parameters={"id": event_id},
    )
    event2 = make_auth_event(
        user_id="user-2",
        email="user2@campus.edu",
        path_parameters={"id": event_id},
    )

    with patch("common.notify.publish_email"):
        res1 = register_handler(event1, None)
        assert res1["statusCode"] == 201

        res2 = register_handler(event2, None)
        assert res2["statusCode"] == 409
        body = json.loads(res2["body"])
        assert body["error"]["code"] == "EVENT_FULL"


def test_register_event_cancelled_fails(dynamodb_table):
    event_id = str(ULID())
    seed_event(dynamodb_table, event_id, capacity=5, status="CANCELLED")

    event = make_auth_event(
        user_id="user-1",
        email="user1@campus.edu",
        path_parameters={"id": event_id},
    )
    resp = register_handler(event, None)
    assert resp["statusCode"] == 409
    assert json.loads(resp["body"])["error"]["code"] == "EVENT_CANCELLED"


def test_register_event_started_fails(dynamodb_table):
    event_id = str(ULID())
    seed_event(dynamodb_table, event_id, capacity=5, hours_from_now=-2)

    event = make_auth_event(
        user_id="user-1",
        email="user1@campus.edu",
        path_parameters={"id": event_id},
    )
    resp = register_handler(event, None)
    assert resp["statusCode"] == 409
    assert json.loads(resp["body"])["error"]["code"] == "EVENT_STARTED"


def test_register_event_not_found(dynamodb_table):
    event_id = str(ULID())
    event = make_auth_event(
        user_id="user-1",
        email="user1@campus.edu",
        path_parameters={"id": event_id},
    )
    resp = register_handler(event, None)
    assert resp["statusCode"] == 404
    assert json.loads(resp["body"])["error"]["code"] == "EVENT_NOT_FOUND"


def test_register_invalid_id(dynamodb_table):
    event = make_auth_event(
        user_id="user-1",
        email="user1@campus.edu",
        path_parameters={"id": "not-a-valid-ulid"},
    )
    resp = register_handler(event, None)
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"]["code"] == "INVALID_ID"


def test_register_missing_email(dynamodb_table):
    event_id = str(ULID())
    seed_event(dynamodb_table, event_id, capacity=5)

    event = make_auth_event(
        user_id="user-1",
        email=None,
        path_parameters={"id": event_id},
    )
    resp = register_handler(event, None)
    assert resp["statusCode"] == 401
    assert json.loads(resp["body"])["error"]["code"] == "TOKEN_MISSING_EMAIL"


def test_register_sns_failure_still_succeeds(dynamodb_table):
    event_id = str(ULID())
    seed_event(dynamodb_table, event_id, capacity=5)

    event = make_auth_event(
        user_id="user-1",
        email="user1@campus.edu",
        path_parameters={"id": event_id},
    )

    with patch("handlers.register.publish_email", side_effect=Exception("SNS error")):
        resp = register_handler(event, None)

    assert resp["statusCode"] == 201


def test_unregister_happy_path(dynamodb_table):
    event_id = str(ULID())
    seed_event(dynamodb_table, event_id, capacity=5, seats_taken=1)

    table = boto3.resource("dynamodb", region_name="ap-south-2").Table(TABLE_NAME)
    table.put_item(
        Item={
            "PK": event_pk(event_id),
            "SK": user_sk("user-1"),
            "entity": "REGISTRATION",
            "GSI1PK": "USER#user-1",
            "GSI1SK": "2026-12-01#id",
            "email": "user1@campus.edu",
        }
    )

    event = make_auth_event(
        user_id="user-1",
        email="user1@campus.edu",
        path_parameters={"id": event_id},
    )
    resp = unregister_handler(event, None)
    assert resp["statusCode"] == 204

    # Verify registration deleted and seatsTaken decremented
    ev_item = table.get_item(Key={"PK": event_pk(event_id), "SK": META_SK})["Item"]
    assert int(ev_item["seatsTaken"]) == 0

    reg_resp = table.get_item(Key={"PK": event_pk(event_id), "SK": user_sk("user-1")})
    assert "Item" not in reg_resp


def test_unregister_not_registered_fails(dynamodb_table):
    event_id = str(ULID())
    seed_event(dynamodb_table, event_id, capacity=5, seats_taken=0)

    event = make_auth_event(
        user_id="user-1",
        email="user1@campus.edu",
        path_parameters={"id": event_id},
    )
    resp = unregister_handler(event, None)
    assert resp["statusCode"] == 404
    assert json.loads(resp["body"])["error"]["code"] == "NOT_REGISTERED"


def test_list_my_registrations(dynamodb_table):
    event_id1 = str(ULID())
    seed_event(dynamodb_table, event_id1, capacity=5)

    table = boto3.resource("dynamodb", region_name="ap-south-2").Table(TABLE_NAME)
    table.put_item(
        Item={
            "PK": event_pk(event_id1),
            "SK": user_sk("user-1"),
            "entity": "REGISTRATION",
            "GSI1PK": "USER#user-1",
            "GSI1SK": "2026-12-01#id1",
            "eventTitle": "Hackathon 1",
            "startsAt": "2026-12-01T10:00:00Z",
            "registeredAt": "2026-11-01T10:00:00Z",
        }
    )

    event = make_auth_event(
        user_id="user-1",
        email="user1@campus.edu",
    )
    resp = list_my_registrations_handler(event, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert len(body["registrations"]) == 1
    assert body["registrations"][0]["eventId"] == event_id1
    assert body["registrations"][0]["eventTitle"] == "Hackathon 1"
