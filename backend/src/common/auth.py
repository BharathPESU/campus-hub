"""Cognito JWT claim parsing and authorization helpers."""

import re
from dataclasses import dataclass
from typing import Any

from common.errors import Forbidden, Unauthorized


@dataclass
class User:
    sub: str
    email: str | None
    groups: list[str]


def parse_groups(raw: Any) -> list[str]:
    """Parse cognito:groups claim into a list of group strings.

    HTTP API v2 delivers cognito:groups as a string like "[admin student]",
    a JSON array, or a plain string.
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    s = str(raw).strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1].strip()
    if not s:
        return []
    return [g for g in re.split(r"[\s,]+", s) if g]


def get_user(event: dict[str, Any]) -> User | None:
    """Extract authenticated User from event claims, requiring token_use == 'id'."""
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims")
    if not claims or "sub" not in claims:
        return None
    if claims.get("token_use") != "id":
        raise Unauthorized("TOKEN_WRONG_TYPE", "Send the ID token.")
    return User(
        sub=claims["sub"],
        email=claims.get("email"),
        groups=parse_groups(claims.get("cognito:groups")),
    )


def require_user(event: dict[str, Any], need_email: bool = False) -> User:
    """Enforce authenticated user, optionally requiring email claim."""
    u = get_user(event)
    if u is None:
        raise Unauthorized("UNAUTHENTICATED", "Sign in required.")
    if need_email and not u.email:
        raise Unauthorized("TOKEN_MISSING_EMAIL", "Token has no email.")
    return u


def require_admin(event: dict[str, Any]) -> User:
    """Enforce that the authenticated user belongs to the 'admin' group."""
    u = require_user(event)
    if "admin" not in u.groups:
        raise Forbidden("ADMIN_ONLY", "Admins only.")
    return u
