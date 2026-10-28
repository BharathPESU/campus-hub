"""Date and time utilities. All internal dates are UTC ISO-8601 strings."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from common.errors import ValidationError

FMT = "%Y-%m-%dT%H:%M:%SZ"


def now_utc() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    """Format timezone-aware datetime to standard UTC ISO-8601 string."""
    if dt.tzinfo is None:
        raise ValidationError("INVALID_DATETIME", "datetime must include a timezone")
    return dt.astimezone(timezone.utc).strftime(FMT)


def parse_iso(s: str) -> datetime:
    """Parse ISO datetime string to timezone-aware UTC datetime.

    Accepts '2026-11-20T09:00:00Z' or with offset like '+05:30'. Rejects naive datetimes.
    """
    if not isinstance(s, str):
        raise ValidationError("INVALID_DATETIME", "datetime must be a string")
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise ValidationError("INVALID_DATETIME", "Invalid ISO datetime format")
    if dt.tzinfo is None:
        raise ValidationError("INVALID_DATETIME", "startsAt must include a timezone")
    return dt.astimezone(timezone.utc)


def display_ist(iso: str) -> str:
    """Convert UTC ISO string to human-friendly Indian Standard Time for emails."""
    return parse_iso(iso).astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %I:%M %p IST")
