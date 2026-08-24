from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, RequireOperator
from app.models.audit import Notification
from app.schemas.applications import NotificationOut
from app.schemas.common import Page
from app.services import notifications as service

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=Page[NotificationOut])
def list_notifications(
    db: DbSession,
    user: CurrentUser,
    unread_only: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[NotificationOut]:
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    total = int(db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one())
    rows = list(
        db.execute(
            stmt.order_by(Notification.created_at.desc()).limit(limit).offset(offset)
        ).scalars()
    )
    return Page[NotificationOut](
        items=[NotificationOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/unread-count", response_model=dict)
def unread_count(db: DbSession, user: CurrentUser) -> dict:
    return {"unread": service.unread_count(db, user.id)}


@router.post("/read", response_model=dict)
def mark_read(
    db: DbSession, user: RequireOperator, notification_ids: list[uuid.UUID] | None = None
) -> dict:
    return {"marked": service.mark_read(db, user.id, notification_ids)}


@router.get("/digest", response_model=dict)
def digest(db: DbSession, user: CurrentUser, hours: int = Query(default=24, ge=1, le=168)) -> dict:
    return service.build_digest(db, user.id, hours=hours)


@router.post("/digest/send", response_model=NotificationOut)
def send_digest(db: DbSession, user: RequireOperator, hours: int = 24) -> Notification:
    return service.send_digest(db, user.id, hours=hours)
