from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, RequireOwner
from app.models.audit import AuditLog
from app.schemas.applications import AuditOut
from app.schemas.common import Page
from app.services import audit as service

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=Page[AuditOut])
def list_audit(
    db: DbSession,
    user: CurrentUser,
    action: str | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Page[AuditOut]:
    """Read-only. There is no endpoint that edits or deletes an audit entry."""
    stmt = select(AuditLog).where(AuditLog.user_id == user.id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if object_type:
        stmt = stmt.where(AuditLog.object_type == object_type)
    if object_id:
        stmt = stmt.where(AuditLog.object_id == object_id)
    total = int(db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one())
    rows = list(
        db.execute(stmt.order_by(AuditLog.seq.desc()).limit(limit).offset(offset)).scalars()
    )
    return Page[AuditOut](
        items=[AuditOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get("/verify", response_model=dict)
def verify(db: DbSession, user: RequireOwner) -> dict:
    """Re-walk the hash chain and report the first break, if any."""
    return service.verify_chain(db)
