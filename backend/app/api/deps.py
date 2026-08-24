"""FastAPI dependencies: auth, RBAC, rate limiting, current-user resolution."""

from __future__ import annotations

import uuid
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.core.config import settings
from app.core.logging import actor_id_var
from app.core.security import Role, constant_time_equals, decode_token, role_allows
from app.db.session import get_db
from app.models.profile import CandidateProfile
from app.models.user import AgentSettings, User

bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def _too_many(remaining: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Rate limit exceeded. Slow down.",
        headers={"Retry-After": "60", "X-RateLimit-Remaining": str(remaining)},
    )


def rate_limit(request: Request) -> None:
    """Per client IP and path. Mounted on the whole v1 router, not just /auth."""
    limit = (
        settings.rate_limit_auth_per_minute
        if request.url.path.endswith(("/login", "/register", "/refresh"))
        else settings.rate_limit_per_minute
    )
    allowed, remaining = ratelimit.check(f"{client_ip(request)}:{request.url.path}", limit=limit)
    if not allowed:
        raise _too_many(remaining)


def rate_limit_account(identifier: str) -> None:
    """Second limiter keyed on the account, not the client address.

    `client_ip` trusts X-Forwarded-For (required behind a reverse proxy), which
    an attacker can rotate freely to defeat a purely IP-keyed limit. Password
    guessing against one account is throttled here regardless of source address.
    """
    key = (identifier or "").strip().lower()
    if not key:
        return
    allowed, remaining = ratelimit.check(
        f"account:{key}", limit=settings.rate_limit_auth_per_minute
    )
    if not allowed:
        raise _too_many(remaining)


def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired") from None
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {exc}") from None

    try:
        subject = uuid.UUID(str(payload.get("sub", "")))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token subject") from None

    user = db.get(User, subject)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User is inactive or unknown")
    actor_id_var.set(str(user.id))
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(required: Role):
    def _dependency(user: CurrentUser) -> User:
        if not role_allows(user.role, required):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"This action requires the '{required.value}' role; you have '{user.role}'.",
            )
        return user

    return _dependency


RequireOwner = Annotated[User, Depends(require_role(Role.OWNER))]
RequireOperator = Annotated[User, Depends(require_role(Role.OPERATOR))]


def get_profile(db: DbSession, user: CurrentUser) -> CandidateProfile:
    profile = db.execute(
        select(CandidateProfile).where(CandidateProfile.user_id == user.id)
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No profile yet. Create one with PUT /api/v1/profile before using the agent.",
        )
    return profile


def get_agent_settings(db: DbSession, user: CurrentUser) -> AgentSettings:
    row = db.execute(
        select(AgentSettings).where(AgentSettings.user_id == user.id)
    ).scalar_one_or_none()
    if row is None:
        row = AgentSettings(
            user_id=user.id,
            auto_submit_min_score=settings.auto_submit_min_score,
            daily_application_limit=settings.daily_application_limit,
            job_max_age_hours=settings.job_max_age_hours,
            discovery_interval_minutes=settings.discovery_interval_minutes,
        )
        db.add(row)
        db.flush()
    return row


def assistant_auth(
    x_assistant_token: Annotated[str | None, Header(alias="X-Assistant-Token")] = None,
) -> bool:
    """Shared-secret auth for the local browser assistant.

    The assistant runs on the user's own machine and never receives a user JWT;
    it authenticates with a token from the environment and can only reach the
    narrow /assistant/* surface.
    """
    expected = settings.browser_assistant_token
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "BROWSER_ASSISTANT_TOKEN is not configured; the assistant API is disabled.",
        )
    if not x_assistant_token or not constant_time_equals(x_assistant_token, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid assistant token")
    return True


AssistantAuth = Annotated[bool, Depends(assistant_auth)]
