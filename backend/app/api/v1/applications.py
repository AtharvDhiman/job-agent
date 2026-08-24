from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, RequireOperator
from app.core.enums import ApplicationStatus
from app.models.application import (
    Application,
    ApplicationAnswer,
    SubmissionAttempt,
)
from app.models.job import Job, JobMatch
from app.schemas.applications import (
    AnswerOut,
    AnswerUpdateIn,
    ApplicationDetailOut,
    ApplicationDocumentOut,
    ApplicationOut,
    DecisionIn,
    DraftIn,
    DraftOut,
    MarkSubmittedIn,
    StageIn,
    SubmissionAttemptOut,
)
from app.schemas.common import Page
from app.schemas.jobs import JobOut
from app.services import application_workflow as workflow
from app.services import audit

router = APIRouter(tags=["applications"])


def _owned(db, user, application_id: uuid.UUID) -> Application:
    application = db.get(Application, application_id)
    if application is None or application.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    return application


@router.post("/applications/draft", response_model=DraftOut, status_code=status.HTTP_201_CREATED)
def draft(payload: DraftIn, db: DbSession, user: RequireOperator) -> DraftOut:
    """Generate documents and answers, then run the policy gate.

    The response tells you exactly what will happen next and why: either the
    application is queued for auto-submission, or a review task was created.
    """
    job = db.get(Job, payload.job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    match = db.execute(
        select(JobMatch).where(JobMatch.user_id == user.id, JobMatch.job_id == job.id)
    ).scalar_one_or_none()

    result = workflow.draft_application(
        db, user, job, match, include_cover_letter=payload.include_cover_letter
    )
    return DraftOut(
        application=ApplicationOut.model_validate(result.application),
        policy=result.decision.as_dict(),
        review_task_id=result.review_task.id if result.review_task else None,
        validation_errors=result.validation_errors,
        blocking_questions=result.blocking_questions,
    )


@router.get("/applications", response_model=Page[ApplicationOut])
def list_applications(
    db: DbSession,
    user: CurrentUser,
    status_filter: str | None = Query(default=None, alias="status"),
    stage: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[ApplicationOut]:
    stmt = select(Application).where(Application.user_id == user.id)
    if status_filter:
        stmt = stmt.where(Application.status == status_filter)
    if stage:
        stmt = stmt.where(Application.pipeline_stage == stage)
    total = int(db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one())
    rows = list(
        db.execute(
            stmt.order_by(Application.updated_at.desc()).limit(limit).offset(offset)
        ).scalars()
    )
    return Page[ApplicationOut](
        items=[ApplicationOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/applications/{application_id}", response_model=ApplicationDetailOut)
def get_application(
    application_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> ApplicationDetailOut:
    application = _owned(db, user, application_id)
    job = db.get(Job, application.job_id)
    return ApplicationDetailOut(
        **ApplicationOut.model_validate(application).model_dump(),
        job=JobOut.model_validate(job),
        answers=[AnswerOut.model_validate(a) for a in application.answers],
        documents=[ApplicationDocumentOut.model_validate(d) for d in application.documents],
    )


@router.patch("/applications/{application_id}/answers", response_model=list[AnswerOut])
def update_answers(
    application_id: uuid.UUID,
    payload: list[AnswerUpdateIn],
    db: DbSession,
    user: RequireOperator,
) -> list[ApplicationAnswer]:
    """Answer the questions the agent refused to guess."""
    application = _owned(db, user, application_id)
    by_id = {a.id: a for a in application.answers}
    updated = []
    for item in payload:
        answer = by_id.get(item.answer_id)
        if answer is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Answer {item.answer_id} not found")
        answer.answer_value = item.answer_value
        answer.source_fact_id = item.source_fact_id
        answer.needs_human = False
        answer.confidence = 100
        answer.reason = "Answered by the account owner"
        updated.append(answer)
    db.flush()
    audit.record(
        db,
        "application.answers_updated",
        user_id=user.id,
        actor=user.email,
        object_type="application",
        object_id=str(application.id),
        payload={"count": len(updated)},
    )
    return updated


@router.post("/applications/{application_id}/approve", response_model=ApplicationOut)
def approve(
    application_id: uuid.UUID, payload: DecisionIn, db: DbSession, user: RequireOperator
) -> Application:
    application = _owned(db, user, application_id)
    unanswered = [a for a in application.answers if a.required and a.needs_human]
    if unanswered:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Answer the required questions first: "
            + "; ".join(a.question_text for a in unanswered[:5]),
        )
    return workflow.approve(db, application, user, payload.note)


@router.post("/applications/{application_id}/reject", response_model=ApplicationOut)
def reject(
    application_id: uuid.UUID, payload: DecisionIn, db: DbSession, user: RequireOperator
) -> Application:
    return workflow.reject(db, _owned(db, user, application_id), user, payload.note)


@router.post("/applications/{application_id}/mark-submitted", response_model=ApplicationOut)
def mark_submitted(
    application_id: uuid.UUID, payload: MarkSubmittedIn, db: DbSession, user: RequireOperator
) -> Application:
    """Record that YOU submitted this application.

    The browser assistant never reports a submission it did not make, so after an
    assisted-autofill hand-off (and after any application you simply applied to
    yourself from a review task) this is how the application leaves the queue.
    The attempt is stored as `submitted_by_human` and the audit entry names you,
    not the assistant.
    """
    application = _owned(db, user, application_id)
    if application.status == ApplicationStatus.SUBMITTED.value:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This application is already recorded as submitted."
        )
    return workflow.record_human_submission(
        db,
        application,
        user,
        confirmation_number=payload.confirmation_number,
        note=payload.note,
    )


@router.post("/applications/{application_id}/stage", response_model=ApplicationOut)
def set_stage(
    application_id: uuid.UUID, payload: StageIn, db: DbSession, user: RequireOperator
) -> Application:
    """Move the application along your pipeline (interview, offer, rejected...)."""
    application = _owned(db, user, application_id)
    application.pipeline_stage = payload.pipeline_stage.value
    db.flush()
    audit.record(
        db,
        "application.stage_changed",
        user_id=user.id,
        actor=user.email,
        object_type="application",
        object_id=str(application.id),
        payload={"stage": payload.pipeline_stage.value, "note": payload.note[:300]},
    )
    return application


@router.get("/applications/{application_id}/attempts", response_model=list[SubmissionAttemptOut])
def list_attempts(
    application_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> list[SubmissionAttempt]:
    application = _owned(db, user, application_id)
    return sorted(application.attempts, key=lambda a: a.attempt_number)
