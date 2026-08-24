from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, RequireOperator
from app.core.enums import AuditAction, ReviewStatus
from app.models.application import ReviewTask
from app.schemas.applications import DecisionIn, ReviewTaskOut
from app.schemas.common import Page
from app.services import application_workflow as workflow
from app.services import audit

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.get("", response_model=Page[ReviewTaskOut])
def list_reviews(
    db: DbSession,
    user: CurrentUser,
    status_filter: str = Query(default=ReviewStatus.OPEN.value, alias="status"),
    reason: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[ReviewTaskOut]:
    stmt = select(ReviewTask).where(ReviewTask.user_id == user.id)
    if status_filter and status_filter != "all":
        stmt = stmt.where(ReviewTask.status == status_filter)
    if reason:
        stmt = stmt.where(ReviewTask.reason == reason)
    total = int(db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one())
    rows = list(
        db.execute(
            stmt.order_by(ReviewTask.created_at.desc()).limit(limit).offset(offset)
        ).scalars()
    )
    return Page[ReviewTaskOut](
        items=[ReviewTaskOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{task_id}", response_model=ReviewTaskOut)
def get_review(task_id: uuid.UUID, db: DbSession, user: CurrentUser) -> ReviewTask:
    task = db.get(ReviewTask, task_id)
    if task is None or task.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review task not found")
    return task


@router.post("/{task_id}/approve", response_model=ReviewTaskOut)
def approve_review(
    task_id: uuid.UUID, payload: DecisionIn, db: DbSession, user: RequireOperator
) -> ReviewTask:
    """Approving a review approves its application, if it has one."""
    task = get_review(task_id, db, user)
    if task.status != ReviewStatus.OPEN.value:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Task is already {task.status}")
    if task.application is not None:
        workflow.approve(db, task.application, user, payload.note)
    else:
        task.status = ReviewStatus.APPROVED.value
        task.resolved_at = datetime.now(UTC)
        task.resolution_note = payload.note[:1000]
    db.flush()
    audit.record(
        db,
        AuditAction.REVIEW_RESOLVED,
        user_id=user.id,
        actor=user.email,
        object_type="review_task",
        object_id=str(task.id),
        payload={"resolution": "approved", "note": payload.note[:300]},
    )
    return task


@router.post("/{task_id}/reject", response_model=ReviewTaskOut)
def reject_review(
    task_id: uuid.UUID, payload: DecisionIn, db: DbSession, user: RequireOperator
) -> ReviewTask:
    task = get_review(task_id, db, user)
    if task.status != ReviewStatus.OPEN.value:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Task is already {task.status}")
    if task.application is not None:
        workflow.reject(db, task.application, user, payload.note)
    task.status = ReviewStatus.REJECTED.value
    task.resolved_at = datetime.now(UTC)
    task.resolution_note = payload.note[:1000]
    db.flush()
    audit.record(
        db,
        AuditAction.REVIEW_RESOLVED,
        user_id=user.id,
        actor=user.email,
        object_type="review_task",
        object_id=str(task.id),
        payload={"resolution": "rejected", "note": payload.note[:300]},
    )
    return task
