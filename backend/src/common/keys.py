"""DynamoDB partition and sort key builders."""


def event_pk(event_id: str) -> str:
    """Return primary partition key for an event item or event child."""
    return f"EVENT#{event_id}"


META_SK = "META"


def user_sk(sub: str) -> str:
    """Return primary sort key for a registration item."""
    return f"USER#{sub}"


def user_gsi_pk(sub: str) -> str:
    """Return GSI1 partition key for a student's registrations."""
    return f"USER#{sub}"


EVENTS_GSI_PK = "EVENTS"


def gsi_sk(starts_at: str, event_id: str) -> str:
    """Return GSI1 sort key for time-ordered queries."""
    return f"{starts_at}#{event_id}"


GSI1 = "GSI1"
