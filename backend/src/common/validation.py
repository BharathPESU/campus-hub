"""Input validation models and functions for API requests."""

from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from common.errors import ValidationError
from common.timeutil import now_utc, parse_iso, to_iso


class CreateEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    location: str = Field(min_length=1, max_length=120)
    startsAt: str
    capacity: StrictInt = Field(ge=1, le=500)

    @field_validator("title", "location")
    @classmethod
    def check_non_empty_strip(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Field cannot be empty or only whitespace")
        return s

    @field_validator("description")
    @classmethod
    def check_desc_strip(cls, v: str) -> str:
        return v.strip()


def validate_create_event(body: dict[str, Any]) -> dict[str, Any]:
    """Validate event creation payload.

    Enforces bounds on title, description, location, capacity (strict int),
    and ensures startsAt is in the future within 365 days, returning normalized UTC ISO.
    """
    if not isinstance(body, dict):
        raise ValidationError("INVALID_FIELD", "Request body must be a dictionary.")

    try:
        model = CreateEventIn(**body)
    except Exception as e:
        # Avoid echoing sensitive input in error messages
        raise ValidationError("INVALID_FIELD", f"Validation error: {e}") from None

    starts = parse_iso(model.startsAt)
    now = now_utc()
    if starts <= now:
        raise ValidationError("INVALID_DATETIME", "Event start time must be in the future.")
    if starts > now + timedelta(days=365):
        raise ValidationError("INVALID_DATETIME", "Event start time must be within 365 days.")

    data = model.model_dump()
    data["startsAt"] = to_iso(starts)
    return data
