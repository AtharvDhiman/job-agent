"""Password hashing, JWTs, and role-based access control."""

from __future__ import annotations

import hmac
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import settings

_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
ALGORITHM = "HS256"


class Role(StrEnum):
    OWNER = "owner"  # full control, including authorizations and erasure
    OPERATOR = "operator"  # can review and approve, cannot change auth grants
    VIEWER = "viewer"  # read-only
    SERVICE = "service"  # browser assistant and workers, narrowly scoped


#: Human roles form a ladder: OWNER outranks OPERATOR outranks VIEWER.
#: SERVICE is deliberately NOT on that ladder. It is the machine identity for the
#: browser assistant and the workers, it authenticates with a shared secret rather
#: than a user JWT, and it must never be satisfiable by a human role (nor satisfy
#: one). Giving it rank 0 -- the same rank as VIEWER -- made `role_allows` return
#: True in both directions, so a VIEWER passed a SERVICE check and a SERVICE
#: principal passed every read-only check in the app.
ROLE_RANK = {Role.VIEWER: 0, Role.OPERATOR: 1, Role.OWNER: 2}


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters")
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(hashed: str) -> bool:
    try:
        return _hasher.check_needs_rehash(hashed)
    except (InvalidHashError, ValueError):
        return True


def _encode(payload: dict[str, Any]) -> str:
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_access_token(*, user_id: str, role: str, extra: dict[str, Any] | None = None) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_ttl_minutes)).timestamp()),
        "jti": uuid.uuid4().hex,
        **(extra or {}),
    }
    return _encode(payload)


def create_refresh_token(*, user_id: str) -> tuple[str, str]:
    """Returns (token, jti). The jti is persisted so the token can be revoked."""
    now = datetime.now(UTC)
    jti = uuid.uuid4().hex
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=settings.refresh_token_ttl_days)).timestamp()),
        "jti": jti,
    }
    return _encode(payload), jti


def decode_token(token: str, *, expected_type: str | None = None) -> dict[str, Any]:
    payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    if expected_type and payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(f"Expected a {expected_type} token")
    return payload


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def role_allows(actual: str, required: Role) -> bool:
    """True when `actual` satisfies `required`.

    Only the OWNER > OPERATOR > VIEWER ladder is comparable. SERVICE is outside
    it: a service principal satisfies nothing but SERVICE, and nothing but
    SERVICE satisfies SERVICE.
    """
    try:
        actual_role = Role(actual)
    except ValueError:
        return False
    if required is Role.SERVICE or actual_role is Role.SERVICE:
        return required is Role.SERVICE and actual_role is Role.SERVICE
    return ROLE_RANK[actual_role] >= ROLE_RANK[required]
