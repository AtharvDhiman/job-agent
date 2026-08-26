from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, RequireOwner, get_agent_settings
from app.connectors import registry
from app.core.enums import AuditAction, SubmissionPolicy
from app.models.user import AgentSettings, PlatformAuthorization
from app.schemas.settings import (
    AUTHORIZATION_ACKNOWLEDGEMENT,
    AgentSettingsIn,
    AgentSettingsOut,
    AuthorizationIn,
    AuthorizationOut,
    PauseIn,
)
from app.services import audit
from app.services.policy import (
    HARD_PROHIBITED_PLATFORMS,
    is_hard_prohibited,
    normalize_platform_key,
)

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=AgentSettingsOut)
def get_settings(
    row: AgentSettings = Depends(get_agent_settings),
) -> AgentSettings:
    return row


@router.patch("", response_model=AgentSettingsOut)
def update_settings(
    payload: AgentSettingsIn,
    db: DbSession,
    user: RequireOwner,
    row: AgentSettings = Depends(get_agent_settings),
) -> AgentSettings:
    data = payload.model_dump(exclude_unset=True, exclude_none=True)
    for key, value in data.items():
        setattr(row, key, value)
    db.flush()
    audit.record(
        db,
        AuditAction.SETTINGS_UPDATED,
        user_id=user.id,
        actor=user.email,
        object_type="agent_settings",
        object_id=str(row.id),
        payload={"changed": data},
    )
    return row


@router.post("/pause", response_model=AgentSettingsOut)
def pause(
    payload: PauseIn,
    db: DbSession,
    user: CurrentUser,
    row: AgentSettings = Depends(get_agent_settings),
) -> AgentSettings:
    """Instant kill-switch. Any authenticated role may pause; only owners resume."""
    row.automation_enabled = False
    row.paused_reason = payload.reason
    db.flush()
    audit.record(
        db,
        AuditAction.AUTOMATION_PAUSED,
        user_id=user.id,
        actor=user.email,
        object_type="agent_settings",
        object_id=str(row.id),
        payload={"reason": payload.reason},
    )
    return row


@router.post("/resume", response_model=AgentSettingsOut)
def resume(
    db: DbSession, user: RequireOwner, row: AgentSettings = Depends(get_agent_settings)
) -> AgentSettings:
    row.automation_enabled = True
    row.paused_reason = ""
    db.flush()
    audit.record(
        db,
        AuditAction.AUTOMATION_RESUMED,
        user_id=user.id,
        actor=user.email,
        object_type="agent_settings",
        object_id=str(row.id),
    )
    return row


# ------------------------------------------------------------ authorizations
@router.get("/authorizations", response_model=list[AuthorizationOut])
def list_authorizations(db: DbSession, user: CurrentUser) -> list[PlatformAuthorization]:
    return list(
        db.execute(
            select(PlatformAuthorization).where(PlatformAuthorization.user_id == user.id)
        ).scalars()
    )


@router.get("/authorizations/acknowledgement", response_model=dict)
def acknowledgement_text() -> dict:
    """The exact phrase that must be typed to authorize automated submission."""
    return {
        "acknowledgement": AUTHORIZATION_ACKNOWLEDGEMENT,
        "note": (
            "Automation is off by default for every platform. Granting it means you have "
            "read that platform's terms and accept responsibility for automated submissions "
            "made on your behalf. It can be revoked at any time and is ignored while the "
            "kill-switch is off."
        ),
        "never_automatable": sorted(HARD_PROHIBITED_PLATFORMS),
    }


@router.post("/authorizations", response_model=AuthorizationOut)
def grant_authorization(
    payload: AuthorizationIn, db: DbSession, user: RequireOwner
) -> PlatformAuthorization:
    """Explicitly authorize automation for ONE platform. Owner role only."""
    platform_key = normalize_platform_key(payload.platform_key)
    if is_hard_prohibited(platform_key):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"{platform_key} prohibits automated applications in its terms. "
            "This cannot be enabled. Matched roles will always become review tasks.",
        )
    try:
        connector = registry.get(platform_key)
    except Exception:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Unknown platform '{payload.platform_key}'"
        ) from None
    if connector.submission_policy_default == SubmissionPolicy.PROHIBITED:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"{connector.display_name} is registered as prohibited for automated submission.",
        )
    if not connector.browser_submission_supported:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"{connector.display_name} is discovery and review only; it has no supported "
            "browser auto-submit workflow.",
        )

    row = db.execute(
        select(PlatformAuthorization).where(
            PlatformAuthorization.user_id == user.id,
            PlatformAuthorization.platform_key == platform_key,
        )
    ).scalar_one_or_none()
    if row is None:
        row = PlatformAuthorization(user_id=user.id, platform_key=platform_key)
        db.add(row)
    row.policy = payload.policy.value
    row.acknowledgement_text = payload.acknowledgement
    row.granted_at = datetime.now(UTC)
    row.revoked_at = None
    row.notes = payload.notes
    db.flush()
    audit.record(
        db,
        AuditAction.AUTHORIZATION_GRANTED,
        user_id=user.id,
        actor=user.email,
        object_type="platform_authorization",
        object_id=str(row.id),
        payload={
            "platform": platform_key,
            "policy": payload.policy.value,
            "acknowledgement_recorded": True,
        },
    )
    return row


@router.delete("/authorizations/{platform_key}", response_model=AuthorizationOut)
def revoke_authorization(
    platform_key: str, db: DbSession, user: RequireOwner
) -> PlatformAuthorization:
    platform_key = normalize_platform_key(platform_key)
    row = db.execute(
        select(PlatformAuthorization).where(
            PlatformAuthorization.user_id == user.id,
            PlatformAuthorization.platform_key == platform_key,
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No authorization for that platform")
    row.revoked_at = datetime.now(UTC)
    row.policy = SubmissionPolicy.REVIEW_REQUIRED.value
    db.flush()
    audit.record(
        db,
        AuditAction.AUTHORIZATION_REVOKED,
        user_id=user.id,
        actor=user.email,
        object_type="platform_authorization",
        object_id=str(row.id),
        payload={"platform": platform_key},
    )
    return row
