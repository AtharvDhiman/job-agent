from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select, update

from app.api.deps import CurrentUser, DbSession, client_ip, rate_limit_account
from app.core.config import settings
from app.core.enums import AuditAction
from app.core.security import (
    Role,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.models.profile import CandidateProfile
from app.models.user import AgentSettings, RefreshToken, User
from app.schemas.auth import LoginIn, LogoutIn, RefreshIn, RegisterIn, TokenOut, UserOut
from app.services import audit

# The IP+path limiter is mounted once on the parent api_router (see
# app/api/v1/__init__.py) so it covers every route rather than only /auth.
router = APIRouter(prefix="/auth", tags=["auth"])

MAX_FAILED_LOGINS = 8
LOCKOUT_MINUTES = 15


def _issue(db, user: User, request: Request) -> TokenOut:
    access = create_access_token(user_id=str(user.id), role=user.role)
    refresh, jti = create_refresh_token(user_id=str(user.id))
    db.add(
        RefreshToken(
            user_id=user.id,
            jti=jti,
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days),
            user_agent=request.headers.get("user-agent", "")[:300],
        )
    )
    return TokenOut(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_ttl_minutes * 60,
    )


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterIn, request: Request, db: DbSession) -> TokenOut:
    """Create the first account. Subsequent accounts default to the viewer role."""
    # Compare on the normalised form. `users.email` is stored lowercase and is
    # UNIQUE, so comparing the raw input let "Owner@Example.com" past this check
    # and turned a duplicate registration into an IntegrityError 500.
    email = str(payload.email).strip().lower()
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with that email exists")

    is_first = db.execute(select(User).limit(1)).scalar_one_or_none() is None
    user = User(
        email=email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=Role.OWNER.value if is_first else Role.VIEWER.value,
    )
    db.add(user)
    db.flush()

    db.add(CandidateProfile(user_id=user.id, full_name=payload.full_name, contact_email=user.email))
    db.add(
        AgentSettings(
            user_id=user.id,
            auto_submit_min_score=settings.auto_submit_min_score,
            daily_application_limit=settings.daily_application_limit,
            job_max_age_hours=settings.job_max_age_hours,
            discovery_interval_minutes=settings.discovery_interval_minutes,
            automation_enabled=False,  # always starts paused
        )
    )
    db.flush()
    audit.record(
        db,
        AuditAction.USER_LOGIN,
        user_id=user.id,
        actor=user.email,
        object_type="user",
        object_id=str(user.id),
        ip_address=client_ip(request),
        payload={"event": "register", "role": user.role},
    )
    return _issue(db, user, request)


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, request: Request, db: DbSession) -> TokenOut:
    rate_limit_account(str(payload.email))
    user = db.execute(
        select(User).where(User.email == str(payload.email).strip().lower())
    ).scalar_one_or_none()

    if user and user.locked_until and user.locked_until.replace(tzinfo=UTC) > datetime.now(UTC):
        raise HTTPException(
            status.HTTP_423_LOCKED,
            f"Account locked until {user.locked_until.isoformat()} after repeated failures.",
        )

    if user is None or not verify_password(payload.password, user.hashed_password):
        if user is not None:
            user.failed_login_count += 1
            if user.failed_login_count >= MAX_FAILED_LOGINS:
                user.locked_until = datetime.now(UTC) + timedelta(minutes=LOCKOUT_MINUTES)
                user.failed_login_count = 0
            audit.record(
                db,
                AuditAction.USER_LOGIN_FAILED,
                user_id=user.id,
                actor=str(payload.email),
                outcome="denied",
                ip_address=client_ip(request),
            )
            # Commit BEFORE raising. The get_db dependency rolls the session back
            # when the handler raises, which silently discarded both the counter
            # and the audit row -- the lockout could never fire and a brute-force
            # attempt left no trace.
            db.commit()
        # Same message either way: do not disclose which accounts exist.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is disabled")

    if needs_rehash(user.hashed_password):
        user.hashed_password = hash_password(payload.password)
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = datetime.now(UTC)
    audit.record(
        db,
        AuditAction.USER_LOGIN,
        user_id=user.id,
        actor=user.email,
        object_type="user",
        object_id=str(user.id),
        ip_address=client_ip(request),
    )
    return _issue(db, user, request)


def _revoke_all(db, user_id: uuid.UUID, now: datetime) -> int:
    return int(
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        ).rowcount
        or 0
    )


@router.post("/refresh", response_model=TokenOut)
def refresh(payload: RefreshIn, request: Request, db: DbSession) -> TokenOut:
    try:
        decoded = decode_token(payload.refresh_token, expected_type="refresh")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid refresh token: {exc}") from None

    jti = str(decoded.get("jti") or "")
    now = datetime.now(UTC)
    stored = db.execute(select(RefreshToken).where(RefreshToken.jti == jti)).scalar_one_or_none()
    if stored is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token revoked")

    # Claim the token with a conditional UPDATE rather than a read-then-write:
    # exactly one caller can flip revoked_at away from NULL, so two concurrent
    # refreshes with the same token cannot both mint a new pair.
    claimed = int(
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.jti == jti, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        ).rowcount
        or 0
    )
    if claimed != 1:
        # The presented token was already rotated. That is either a replay or a
        # stolen token racing the legitimate holder; kill the whole family.
        revoked = _revoke_all(db, stored.user_id, now)
        audit.record(
            db,
            AuditAction.USER_LOGIN_FAILED,
            user_id=stored.user_id,
            actor="refresh_reuse",
            outcome="denied",
            ip_address=client_ip(request),
            payload={"event": "refresh_token_reuse", "sessions_revoked": revoked},
        )
        db.commit()
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Refresh token was already used. Every session has been revoked; sign in again.",
        )

    expires_at = stored.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at is not None and expires_at <= now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token expired")

    user = db.get(User, stored.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User is inactive")

    return _issue(db, user, request)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(db: DbSession, user: CurrentUser, payload: LogoutIn | None = None) -> None:
    """Revoke every refresh token for the caller.

    This is deliberately account-wide rather than per-token: the caller proves
    who they are with the access token, and "log me out" should not leave other
    stolen sessions alive. The body is optional and ignored -- a refresh token
    belonging to somebody else can never revoke their session through here.
    """
    _revoke_all(db, user.id, datetime.now(UTC))


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user
