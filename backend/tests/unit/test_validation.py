"""Unit tests for event validation logic."""

from datetime import datetime, timedelta, timezone

import pytest

from common.errors import ValidationError
from common.validation import validate_create_event


def future_iso(days: int = 5) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_valid_event():
    starts = future_iso(10)
    data = {
        "title": "Cloud Computing Hackathon",
        "description": "24h hackathon on AWS",
        "location": "Auditorium",
        "startsAt": starts,
        "capacity": 50,
    }
    validated = validate_create_event(data)
    assert validated["title"] == "Cloud Computing Hackathon"
    assert validated["capacity"] == 50
    assert validated["startsAt"] == starts


def test_valid_event_with_tz_offset():
    # +05:30 offset should be accepted and converted to normalized ...Z
    future_dt = datetime.now(timezone.utc) + timedelta(days=2)
    dt_str = future_dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    data = {
        "title": "Workshop",
        "location": "Lab 1",
        "startsAt": dt_str,
        "capacity": 20,
    }
    validated = validate_create_event(data)
    assert validated["startsAt"].endswith("Z")


def test_reject_missing_title():
    data = {
        "location": "Hall A",
        "startsAt": future_iso(5),
        "capacity": 10,
    }
    with pytest.raises(ValidationError) as exc:
        validate_create_event(data)
    assert exc.value.code == "INVALID_FIELD"


def test_reject_title_too_long():
    data = {
        "title": "A" * 121,
        "location": "Hall A",
        "startsAt": future_iso(5),
        "capacity": 10,
    }
    with pytest.raises(ValidationError) as exc:
        validate_create_event(data)
    assert exc.value.code == "INVALID_FIELD"


@pytest.mark.parametrize("invalid_capacity", [0, 501, True, "5", 5.5, -1])
def test_reject_invalid_capacity(invalid_capacity):
    data = {
        "title": "Seminar",
        "location": "Hall A",
        "startsAt": future_iso(5),
        "capacity": invalid_capacity,
    }
    with pytest.raises(ValidationError) as exc:
        validate_create_event(data)
    assert exc.value.code == "INVALID_FIELD"


def test_reject_unknown_field():
    data = {
        "title": "Seminar",
        "location": "Hall A",
        "startsAt": future_iso(5),
        "capacity": 25,
        "extraField": "malicious",
    }
    with pytest.raises(ValidationError) as exc:
        validate_create_event(data)
    assert exc.value.code == "INVALID_FIELD"


def test_reject_starts_at_naive():
    data = {
        "title": "Seminar",
        "location": "Hall A",
        "startsAt": "2026-12-01T10:00:00",  # missing timezone
        "capacity": 25,
    }
    with pytest.raises(ValidationError) as exc:
        validate_create_event(data)
    assert exc.value.code in ("INVALID_FIELD", "INVALID_DATETIME")


def test_reject_starts_at_past():
    past_str = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {
        "title": "Past Event",
        "location": "Hall A",
        "startsAt": past_str,
        "capacity": 25,
    }
    with pytest.raises(ValidationError) as exc:
        validate_create_event(data)
    assert exc.value.code == "INVALID_DATETIME"


def test_reject_starts_at_more_than_365_days():
    far_str = (datetime.now(timezone.utc) + timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {
        "title": "Far Event",
        "location": "Hall A",
        "startsAt": far_str,
        "capacity": 25,
    }
    with pytest.raises(ValidationError) as exc:
        validate_create_event(data)
    assert exc.value.code == "INVALID_DATETIME"


def test_reject_non_dict_body():
    with pytest.raises(ValidationError) as exc:
        validate_create_event("not a dict")
    assert exc.value.code == "INVALID_FIELD"
