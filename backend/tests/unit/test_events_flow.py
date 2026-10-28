"""Unit tests for event lifecycle, admin flows, reminder scheduling, and email dispatch."""

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws
from ulid import ULID

from common.keys import GSI1, META_SK, event_pk, gsi_sk, user_sk
from handlers.create_event import handler as create_event_handler
from handlers.email_sender import handler as email_sender_handler
from handlers.get_event import handler as get_event_handler
from handlers.health import handler as health_handler
from handlers.list_events import handler as list_events_handler
from handlers.send_reminder import handler as send_reminder_handler
from handlers.start_cancel import handler as start_cancel_handler

TABLE_NAME = "campus-hub-test"


@pytest.fixture(autouse=True)
def setup_env():
    os.environ["TABLE_NAME"] = TABLE_NAME
    os.environ["TOPIC_ARN"] = "arn:aws:sns:ap-south-2:123456789012:test-topic"
    os.environ["SCHEDULE_GROUP"] = "campus-hub-test-reminders"
    os.environ["SCHEDULER_ROLE_ARN"] = "arn:aws:iam::123456789012:role/scheduler-role"
    os.environ["REMINDER_LAMBDA_ARN"] = "arn:aws:lambda:ap-south-2:123456789012:function:reminder"
    os.environ["REMINDER_DLQ_ARN"] = "arn:aws:sqs:ap-south-2:123456789012:reminders-dlq"
    os.environ["CANCEL_STATE_MACHINE_ARN"] = (
        "arn:aws:states:ap-south-2:123456789012:stateMachine:cancel"
    )
    os.environ["AWS_DEFAULT_REGION"] = "ap-south-2"
    os.environ["EMAIL_MODE"] = "log"


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


def make_admin_event(body: dict | None = None, path_parameters: dict | None = None) -> dict:
    return {
        "requestContext": {
            "requestId": "req-admin-1",
            "authorizer": {
                "jwt": {
                    "claims": {
                        "sub": "admin-sub-1",
                        "email": "admin@campus.edu",
                        "token_use": "id",
                        "cognito:groups": "[admin]",
                    }
                }
            },
        },
        "pathParameters": path_parameters or {},
        "body": json.dumps(body) if body is not None else None,
    }


def test_health():
    resp = health_handler({}, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["status"] == "ok"
    assert "time" in body


def test_list_and_get_events(dynamodb_table):
    event_id = str(ULID())
    starts_at = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")

    dynamodb_table.put_item(
        TableName=TABLE_NAME,
        Item={
            "PK": {"S": event_pk(event_id)},
            "SK": {"S": META_SK},
            "entity": {"S": "EVENT"},
            "GSI1PK": {"S": "EVENTS"},
            "GSI1SK": {"S": gsi_sk(starts_at, event_id)},
            "title": {"S": "AI Seminar"},
            "description": {"S": "Intro to LLMs"},
            "location": {"S": "Room 101"},
            "startsAt": {"S": starts_at},
            "capacity": {"N": "50"},
            "seatsTaken": {"N": "10"},
            "status": {"S": "ACTIVE"},
        },
    )

    # Test list_events
    list_resp = list_events_handler({"requestContext": {"requestId": "r1"}}, None)
    assert list_resp["statusCode"] == 200
    events = json.loads(list_resp["body"])["events"]
    assert len(events) == 1
    assert events[0]["eventId"] == event_id
    assert events[0]["seatsLeft"] == 40

    # Test get_event
    get_resp = get_event_handler(
        {"requestContext": {"requestId": "r2"}, "pathParameters": {"id": event_id}}, None
    )
    assert get_resp["statusCode"] == 200
    ev = json.loads(get_resp["body"])
    assert ev["eventId"] == event_id
    assert ev["title"] == "AI Seminar"


def test_create_event_with_schedule(dynamodb_table):
    starts_at = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    event = make_admin_event(
        body={
            "title": "Hackathon 2026",
            "description": "Annual hackathon",
            "location": "Main Hall",
            "startsAt": starts_at,
            "capacity": 100,
        }
    )

    with patch("handlers.create_event._get_scheduler_client") as mock_sched_factory:
        mock_sched = MagicMock()
        mock_sched_factory.return_value = mock_sched

        resp = create_event_handler(event, None)
        assert resp["statusCode"] == 201
        body = json.loads(resp["body"])
        assert body["title"] == "Hackathon 2026"

        mock_sched.create_schedule.assert_called_once()
        call_kwargs = mock_sched.create_schedule.call_args[1]
        assert call_kwargs["GroupName"] == "campus-hub-test-reminders"
        assert call_kwargs["ActionAfterCompletion"] == "DELETE"


def test_create_event_soon_skips_schedule(dynamodb_table):
    # Event starting in 2 hours (< 24 hours lead time)
    starts_at = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    event = make_admin_event(
        body={
            "title": "Flash Talk",
            "location": "Room 202",
            "startsAt": starts_at,
            "capacity": 30,
        }
    )

    with patch("handlers.create_event._get_scheduler_client") as mock_sched_factory:
        mock_sched = MagicMock()
        mock_sched_factory.return_value = mock_sched

        resp = create_event_handler(event, None)
        assert resp["statusCode"] == 201
        mock_sched.create_schedule.assert_not_called()


def test_create_event_scheduler_failure_compensates(dynamodb_table):
    starts_at = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    event = make_admin_event(
        body={
            "title": "Failed Event",
            "location": "Room 202",
            "startsAt": starts_at,
            "capacity": 30,
        }
    )

    with patch("handlers.create_event._get_scheduler_client") as mock_sched_factory:
        mock_sched = MagicMock()
        mock_sched.create_schedule.side_effect = Exception("Scheduler service error")
        mock_sched_factory.return_value = mock_sched

        resp = create_event_handler(event, None)
        assert resp["statusCode"] == 500

    # Ensure table has no orphan item
    table = boto3.resource("dynamodb", region_name="ap-south-2").Table(TABLE_NAME)
    scan_resp = table.scan()
    assert len(scan_resp.get("Items", [])) == 0


def test_create_event_non_admin_forbidden(dynamodb_table):
    starts_at = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    student_event = {
        "requestContext": {
            "requestId": "req-student",
            "authorizer": {
                "jwt": {
                    "claims": {
                        "sub": "student-1",
                        "token_use": "id",
                        "cognito:groups": "[student]",
                    }
                }
            },
        },
        "body": json.dumps(
            {
                "title": "Unauthorized Event",
                "location": "Auditorium",
                "startsAt": starts_at,
                "capacity": 10,
            }
        ),
    }
    resp = create_event_handler(student_event, None)
    assert resp["statusCode"] == 403
    assert json.loads(resp["body"])["error"]["code"] == "ADMIN_ONLY"


def test_start_cancel_idempotent(dynamodb_table):
    event_id = str(ULID())
    starts_at = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

    dynamodb_table.put_item(
        TableName=TABLE_NAME,
        Item={
            "PK": {"S": event_pk(event_id)},
            "SK": {"S": META_SK},
            "status": {"S": "ACTIVE"},
            "title": {"S": "Cancelable Event"},
            "startsAt": {"S": starts_at},
        },
    )

    event = make_admin_event(path_parameters={"id": event_id})

    with patch("handlers.start_cancel._get_sfn_client") as mock_sfn_factory:
        mock_sfn = MagicMock()
        mock_sfn_factory.return_value = mock_sfn

        # First cancellation
        res1 = start_cancel_handler(event, None)
        assert res1["statusCode"] == 202
        assert json.loads(res1["body"])["status"] == "CANCELLING"
        mock_sfn.start_execution.assert_called_once()

        # Second cancellation (idempotent, does not re-invoke SFN)
        mock_sfn.reset_mock()
        res2 = start_cancel_handler(event, None)
        assert res2["statusCode"] == 202
        assert json.loads(res2["body"])["status"] == "CANCELLING"
        mock_sfn.start_execution.assert_not_called()


def test_send_reminder_claim_and_publish(dynamodb_table):
    event_id = str(ULID())
    starts_at = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    table = boto3.resource("dynamodb", region_name="ap-south-2").Table(TABLE_NAME)
    table.put_item(
        Item={
            "PK": event_pk(event_id),
            "SK": META_SK,
            "status": "ACTIVE",
            "title": "Demo Workshop",
            "location": "Lab 3",
            "startsAt": starts_at,
            "reminderStatus": "SCHEDULED",
        }
    )
    table.put_item(
        Item={
            "PK": event_pk(event_id),
            "SK": user_sk("student-1"),
            "email": "student1@campus.edu",
        }
    )

    with patch("handlers.send_reminder.publish_email_batch") as mock_batch:
        # First call: successfully claims, publishes, and marks SENT
        result1 = send_reminder_handler({"eventId": event_id}, None)
        assert result1 == {"sent": 1}
        mock_batch.assert_called_once()

        # Second call: already claimed / sent
        result2 = send_reminder_handler({"eventId": event_id}, None)
        assert result2 == {"skipped": "already_claimed"}


def test_send_reminder_publish_failure_releases_claim(dynamodb_table):
    event_id = str(ULID())
    starts_at = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    table = boto3.resource("dynamodb", region_name="ap-south-2").Table(TABLE_NAME)
    table.put_item(
        Item={
            "PK": event_pk(event_id),
            "SK": META_SK,
            "status": "ACTIVE",
            "title": "Demo Workshop",
            "startsAt": starts_at,
            "reminderStatus": "SCHEDULED",
        }
    )
    table.put_item(
        Item={
            "PK": event_pk(event_id),
            "SK": user_sk("student-1"),
            "email": "student1@campus.edu",
        }
    )

    with patch("handlers.send_reminder.publish_email_batch", side_effect=Exception("SNS Failure")):
        with pytest.raises(Exception):
            send_reminder_handler({"eventId": event_id}, None)

    # Claim should be released back to SCHEDULED
    ev_item = table.get_item(Key={"PK": event_pk(event_id), "SK": META_SK})["Item"]
    assert ev_item["reminderStatus"] == "SCHEDULED"
    assert "reminderSentAt" not in ev_item


def test_send_reminder_zero_registrants_marks_sent(dynamodb_table):
    event_id = str(ULID())
    table = boto3.resource("dynamodb", region_name="ap-south-2").Table(TABLE_NAME)
    table.put_item(
        Item={
            "PK": event_pk(event_id),
            "SK": META_SK,
            "status": "ACTIVE",
            "title": "Empty Event",
            "startsAt": "2026-12-01T10:00:00Z",
            "reminderStatus": "SCHEDULED",
        }
    )

    result = send_reminder_handler({"eventId": event_id}, None)
    assert result == {"sent": 0}
    ev_item = table.get_item(Key={"PK": event_pk(event_id), "SK": META_SK})["Item"]
    assert ev_item["reminderStatus"] == "SENT"


def test_email_sender_log_mode_and_batch_failures():
    # Good record and bad record in the same batch
    event = {
        "Records": [
            {
                "messageId": "msg-good",
                "body": json.dumps(
                    {
                        "to": "alice@campus.edu",
                        "subject": "Hello",
                        "body": "Welcome",
                    }
                ),
            },
            {
                "messageId": "msg-bad",
                "body": json.dumps({"invalid": "data"}),  # missing to, subject, body
            },
        ]
    }

    result = email_sender_handler(event, None)
    assert result == {"batchItemFailures": [{"itemIdentifier": "msg-bad"}]}
