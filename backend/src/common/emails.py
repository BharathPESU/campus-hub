"""Email templates for event registrations and reminders."""

from typing import Any

from common.timeutil import display_ist


def confirmation_email(ev: dict[str, Any], reg: Any) -> tuple[str, str]:
    """Generate subject and plain-text body for registration confirmation."""
    title = ev["title"]
    location = ev.get("location", "TBD")
    starts_at = display_ist(ev["startsAt"])

    subject = f"Registration Confirmed: {title}"
    body = (
        f"Hello,\n\n"
        f"Your registration for '{title}' has been confirmed!\n\n"
        f"Details:\n"
        f"- When: {starts_at}\n"
        f"- Where: {location}\n\n"
        f"We look forward to seeing you.\n"
        f"— Campus Hub Team"
    )
    return subject, body


def reminder_email(ev: dict[str, Any], reg: Any) -> tuple[str, str]:
    """Generate subject and plain-text body for event reminder."""
    title = ev["title"]
    location = ev.get("location", "TBD")
    starts_at = display_ist(ev["startsAt"])

    subject = f"Reminder: {title} starts tomorrow"
    body = (
        f"Hello,\n\n"
        f"This is a friendly reminder that '{title}' will start tomorrow!\n\n"
        f"Details:\n"
        f"- When: {starts_at}\n"
        f"- Where: {location}\n\n"
        f"See you there!\n"
        f"— Campus Hub Team"
    )
    return subject, body
