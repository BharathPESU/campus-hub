"""Unit tests for auth parsing and role enforcement."""

import pytest

from common.auth import get_user, parse_groups, require_admin, require_user
from common.errors import Forbidden, Unauthorized


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, []),
        ("[admin student]", ["admin", "student"]),
        ("admin", ["admin"]),
        ("[admin]", ["admin"]),
        (["admin"], ["admin"]),
        ("", []),
        ("[]", []),
        ("admin, student", ["admin", "student"]),
        (" [admin   student] ", ["admin", "student"]),
    ],
)
def test_parse_groups(raw, expected):
    assert parse_groups(raw) == expected


def test_get_user_valid_id_token():
    event = {
        "requestContext": {
            "authorizer": {
                "jwt": {
                    "claims": {
                        "sub": "user-123",
                        "email": "user@example.com",
                        "token_use": "id",
                        "cognito:groups": "[student]",
                    }
                }
            }
        }
    }
    user = get_user(event)
    assert user is not None
    assert user.sub == "user-123"
    assert user.email == "user@example.com"
    assert user.groups == ["student"]


def test_get_user_rejects_access_token():
    event = {
        "requestContext": {
            "authorizer": {
                "jwt": {
                    "claims": {
                        "sub": "user-123",
                        "token_use": "access",
                    }
                }
            }
        }
    }
    with pytest.raises(Unauthorized) as exc:
        get_user(event)
    assert exc.value.code == "TOKEN_WRONG_TYPE"


def test_get_user_missing_claims():
    assert get_user({}) is None


def test_require_user_unauthenticated():
    with pytest.raises(Unauthorized) as exc:
        require_user({})
    assert exc.value.code == "UNAUTHENTICATED"


def test_require_user_missing_email():
    event = {
        "requestContext": {
            "authorizer": {
                "jwt": {
                    "claims": {
                        "sub": "user-123",
                        "token_use": "id",
                    }
                }
            }
        }
    }
    with pytest.raises(Unauthorized) as exc:
        require_user(event, need_email=True)
    assert exc.value.code == "TOKEN_MISSING_EMAIL"


def test_require_admin():
    admin_event = {
        "requestContext": {
            "authorizer": {
                "jwt": {
                    "claims": {
                        "sub": "admin-1",
                        "email": "admin@example.com",
                        "token_use": "id",
                        "cognito:groups": "[admin]",
                    }
                }
            }
        }
    }
    user = require_admin(admin_event)
    assert user.sub == "admin-1"
    assert "admin" in user.groups

    student_event = {
        "requestContext": {
            "authorizer": {
                "jwt": {
                    "claims": {
                        "sub": "student-1",
                        "email": "student@example.com",
                        "token_use": "id",
                        "cognito:groups": "[student]",
                    }
                }
            }
        }
    }
    with pytest.raises(Forbidden) as exc:
        require_admin(student_event)
    assert exc.value.code == "ADMIN_ONLY"
