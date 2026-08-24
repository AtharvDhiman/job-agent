"""API surface for the LOCAL browser assistant.

The assistant runs on the user's own machine, authenticates with a shared
secret, and can only reach these endpoints. It cannot read the profile, cannot
change settings, and cannot grant itself permission: every task it receives has
already passed services/policy.decide().

If the assistant meets a CAPTCHA, a login wall, bot protection, an unexpected
question, or anything it cannot fill truthfully, it reports that here and the
server converts the attempt into a review task. It never tries again.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import AssistantAuth, DbSession
from app.core.config import settings
from app.core.enums import (
    ApplicationStatus,
    AuditAction,
    ReviewReason,
    SubmissionPolicy,
)
from app.models.application import (
    Application,
    ApplicationAnswer,
    ApplicationDocument,
    SubmissionAttempt,
)
from app.models.job import Job
from app.models.profile import CandidateProfile, CareerFact, Document
from app.models.user import AgentSettings, PlatformAuthorization
from app.services import answers as answers_service
from app.services import application_workflow as workflow
from app.services import audit, fact_guard, policy, storage

router = APIRouter(prefix="/assistant", tags=["assistant"])

ASSISTANT_ABORT_REASONS = {
    "captcha_detected": ReviewReason.CAPTCHA_DETECTED.value,
    "login_required": ReviewReason.LOGIN_REQUIRED.value,
    "bot_protection_detected": ReviewReason.BOT_PROTECTION_DETECTED.value,
    "robots_disallowed": ReviewReason.ROBOTS_DISALLOWED.value,
    "unknown_question": ReviewReason.UNANSWERABLE_QUESTION.value,
    "free_text_question": ReviewReason.FREE_TEXT_QUESTION.value,
    "missing_attachment": ReviewReason.MISSING_ATTACHMENT.value,
    "validation_failed": ReviewReason.VALIDATION_FAILED.value,
    "unsupported_platform": ReviewReason.UNSUPPORTED_PLATFORM.value,
    "submission_error": ReviewReason.SUBMISSION_ERROR.value,
}


class FieldPlan(BaseModel):
    selector_hint: str
    question_external_id: str
    label: str
    value: str
    type: str
    required: bool


class TaskOut(BaseModel):
    application_id: uuid.UUID
    attempt_id: uuid.UUID
    mode: str
    may_click_submit: bool
    apply_url: str
    job_title: str
    company: str
    connector_key: str
    fields: list[FieldPlan]
    attachments: list[dict]
    policy: dict
    guard_rules: dict


class DiscoveredQuestion(BaseModel):
    external_id: str = ""
    text: str
    type: str = "unknown"
    required: bool = False
    options: list[str] = Field(default_factory=list)


class QuestionsIn(BaseModel):
    questions: list[DiscoveredQuestion]


class QuestionsOut(BaseModel):
    answers: list[dict]
    unanswerable: list[dict]
    must_abort: bool


class ResultIn(BaseModel):
    outcome: str = Field(pattern="^(submitted|aborted|failed)$")
    confirmation_number: str = ""
    error_message: str = ""
    abort_reason: str = ""
    guard_findings: list[dict] = Field(default_factory=list)
    filled_fields: list[dict] = Field(default_factory=list)
    receipt: dict = Field(default_factory=dict)
    screenshot_base64: str = ""
    assistant_version: str = ""


@router.get("/health", response_model=dict)
def assistant_health(_: AssistantAuth) -> dict:
    return {
        "status": "ok",
        "global_automation_enabled": settings.automation_global_enabled,
        "server_time": datetime.now(UTC).isoformat(),
    }


GUARD_RULES = {
    "abort_on_captcha": True,
    "abort_on_login_wall": True,
    "abort_on_bot_protection": True,
    "abort_on_unknown_question": True,
    "abort_on_free_text_question": True,
    "respect_robots_txt": True,
    "headless_forbidden": True,
    "never_solve_captcha": True,
    "never_spoof_fingerprint": True,
    "max_runtime_seconds": 180,
    "captcha_markers": [
        "recaptcha",
        "g-recaptcha",
        "h-captcha",
        "hcaptcha",
        "cf-turnstile",
        "turnstile",
        "datadome",
        "px-captcha",
        "perimeterx",
        "kasada",
        "are you a robot",
        "checking your browser",
    ],
    "login_markers": [
        "sign in",
        "log in",
        "create an account",
        "password",
        "authwall",
    ],
}


def _fresh_policy(db, application: Application, job: Job) -> policy.PolicyDecision | None:
    """Re-evaluate the gate at hand-out time; settings may have changed since drafting."""
    agent_settings = db.execute(
        select(AgentSettings).where(AgentSettings.user_id == application.user_id)
    ).scalar_one_or_none()
    if agent_settings is None:
        # No settings row means the account was never configured. Fail closed and
        # skip the application rather than 500 the whole poll.
        return None
    authorization = db.execute(
        select(PlatformAuthorization).where(
            PlatformAuthorization.user_id == application.user_id,
            PlatformAuthorization.platform_key == job.connector_key,
        )
    ).scalar_one_or_none()
    blocking = sum(1 for a in application.answers if a.required and a.needs_human)
    return policy.decide(
        job=job,
        connector_policy=job.submission_policy_default,
        authorization=authorization,
        agent_settings=agent_settings,
        # No match row means no evidence this job fits: score 0, never a pass by
        # default. Only a threshold the user themselves set to 0 can clear it.
        score=_match_score(db, application),
        global_enabled=settings.automation_global_enabled,
        applications_today=workflow.applications_today(db, application.user_id),
        fact_guard_blocked=any(
            f.get("severity") == "block" for f in (application.fact_guard_flags or [])
        ),
        blocking_questions=blocking,
        validation_errors=len(application.validation_errors or []),
    )


def _match_score(db, application: Application) -> int:
    from app.models.job import JobMatch

    match = db.get(JobMatch, application.match_id) if application.match_id else None
    return match.score if match else 0


@router.get("/tasks/next", response_model=TaskOut | None)
def next_task(db: DbSession, _: AssistantAuth, mode: str = Query(default="any")) -> TaskOut | None:
    """Hand out one task the policy gate currently permits. Returns null if none."""
    if not settings.automation_global_enabled:
        return None

    candidates = list(
        db.execute(
            select(Application)
            .where(
                Application.status.in_(
                    [ApplicationStatus.QUEUED.value, ApplicationStatus.APPROVED.value]
                )
            )
            .order_by(Application.approved_at.asc().nullsfirst(), Application.created_at.asc())
            .limit(20)
        ).scalars()
    )

    for application in candidates:
        job = db.get(Job, application.job_id)
        if job is None:
            continue
        if application.attempt_count >= workflow.MAX_ATTEMPTS_PER_APPLICATION:
            # Out of attempts. Retire it so it leaves the queue for good instead
            # of being re-offered on every poll.
            workflow.retire_exhausted(db, application)
            continue

        decision = _fresh_policy(db, application, job)
        if decision is None:
            continue

        # policy.may_hand_out is the only thing allowed to answer "may the
        # assistant touch this page at all". A human approval lifts the bars the
        # user set for themselves (a low score, their daily cap); it never lifts a
        # prohibited platform, a platform they never granted, a paused
        # kill-switch, or content that does not trace to a verified fact.
        if not policy.may_hand_out(
            decision, approved_by_human=application.approved_by_user_id is not None
        ):
            continue
        if mode == "auto_submit" and not decision.may_submit:
            continue

        attempt = workflow.claim_for_attempt(
            db,
            application,
            mode=SubmissionPolicy.AUTO_SUBMIT.value
            if decision.may_submit
            else SubmissionPolicy.ASSISTED_AUTOFILL.value,
        )
        if attempt is None:
            # Another assistant claimed it between the read and the update.
            continue
        audit.record(
            db,
            "assistant.task_issued",
            user_id=application.user_id,
            actor="browser_assistant",
            object_type="application",
            object_id=str(application.id),
            payload={"policy": decision.as_dict(), "attempt": attempt.attempt_number},
        )
        return _build_task(db, application, job, attempt, decision)
    return None


def _build_task(db, application: Application, job: Job, attempt: SubmissionAttempt, decision):
    fields = [
        FieldPlan(
            selector_hint=answer.question_external_id,
            question_external_id=answer.question_external_id,
            label=answer.question_text,
            value=answer.answer_value or "",
            type=answer.question_type,
            required=answer.required,
        )
        for answer in application.answers
        if answer.answer_value and not answer.needs_human
    ]
    attachments = []
    for link in application.documents:
        document = db.get(Document, link.document_id)
        if document is None:
            continue
        attachments.append(
            {
                "role": link.role,
                "document_id": str(document.id),
                "filename": document.filename,
                "content_type": document.content_type,
                "download_path": f"/api/v1/assistant/documents/{document.id}",
            }
        )
    return TaskOut(
        application_id=application.id,
        attempt_id=attempt.id,
        mode=attempt.mode,
        may_click_submit=decision.may_submit,
        apply_url=job.apply_url or job.source_url,
        job_title=job.title,
        company=job.company,
        connector_key=job.connector_key,
        fields=fields,
        attachments=attachments,
        policy=decision.as_dict(),
        guard_rules=GUARD_RULES,
    )


def _open_attempt(db, application_id: uuid.UUID) -> SubmissionAttempt | None:
    """The attempt the assistant is currently working, if any.

    Everything on this surface is scoped through this. The assistant holds one
    shared secret with no user identity attached to it, so "authenticated" must
    never mean "may reach any row": it may only touch the application it was
    handed a task for, while that task is still open.
    """
    return db.execute(
        select(SubmissionAttempt)
        .where(
            SubmissionAttempt.application_id == application_id,
            SubmissionAttempt.outcome == "pending",
        )
        .order_by(SubmissionAttempt.attempt_number.desc())
        .limit(1)
    ).scalar_one_or_none()


@router.get("/documents/{document_id}")
def fetch_attachment(document_id: uuid.UUID, db: DbSession, _: AssistantAuth):
    from fastapi import Response

    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    # Ownership check. Without this, the shared assistant secret was a read-any
    # -document credential: every user's resume, cover letter and confirmation
    # screenshot was fetchable by id from a single global token.
    attached_to_open_task = db.execute(
        select(ApplicationDocument.id)
        .join(
            SubmissionAttempt,
            SubmissionAttempt.application_id == ApplicationDocument.application_id,
        )
        .where(
            ApplicationDocument.document_id == document.id,
            ApplicationDocument.attached.is_(True),
            SubmissionAttempt.outcome == "pending",
        )
        .limit(1)
    ).scalar_one_or_none()
    if attached_to_open_task is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Document not found, or not attached to a task you are currently working.",
        )

    try:
        content = storage.get_storage().read(document.storage_key)
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(status.HTTP_410_GONE, f"Stored file is missing: {exc}") from None
    return Response(
        content=content,
        media_type=document.content_type,
        headers={
            "Content-Disposition": storage.content_disposition(document.filename),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.post("/tasks/{application_id}/questions", response_model=QuestionsOut)
def resolve_questions(
    application_id: uuid.UUID, payload: QuestionsIn, db: DbSession, _: AssistantAuth
) -> QuestionsOut:
    """The assistant reports the real form fields; the server answers them.

    The assistant never decides an answer itself. Anything the server cannot
    answer from a verified fact comes back as unanswerable, and if any of those
    are required the assistant must abort.
    """
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    if _open_attempt(db, application.id) is None:
        # No open task means this application was never handed to the assistant
        # (or is already finished). Answering questions against it would let the
        # shared secret derive answers from any user's profile on demand.
        raise HTTPException(status.HTTP_409_CONFLICT, "No open attempt for this application")

    profile = db.execute(
        select(CandidateProfile).where(CandidateProfile.user_id == application.user_id)
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "The account behind this application has no profile"
        )
    facts = list(
        db.execute(select(CareerFact).where(CareerFact.profile_id == profile.id)).scalars()
    )

    index = fact_guard.FactIndex(profile, facts)
    new_guard_flags: list[dict] = []
    existing = {a.question_external_id: a for a in application.answers}
    resolved, unanswerable = [], []
    for item in payload.questions:
        question = answers_service.Question(
            external_id=item.external_id or item.text[:120],
            text=item.text,
            type=item.type,
            required=item.required,
            options=item.options,
        )
        answer = answers_service.answer_question(question, profile, facts)
        # Same truthfulness check the drafting path runs. Without it, an answer
        # derived here reached the form without ever being compared to a fact.
        new_guard_flags += workflow.guard_answer(answer, index)

        row = existing.get(question.external_id)
        if row is None:
            row = ApplicationAnswer(
                application_id=application.id,
                question_external_id=question.external_id,
                question_text=question.text,
                question_type=question.type,
                required=question.required,
                options=question.options,
            )
            db.add(row)
        else:
            # The live form is authoritative about what it demands. Required is
            # only ever raised, never lowered: a stored `required=False` would
            # otherwise hide the question from the blocking count in
            # _fresh_policy and from the approval gate.
            row.required = bool(row.required or question.required)
            row.options = question.options or row.options
            row.question_type = question.type or row.question_type
        # A human-supplied answer always wins over a freshly derived one.
        if row.needs_human or not row.answer_value:
            row.answer_value = answer.value
            row.confidence = answer.confidence
            row.needs_human = answer.needs_human
            row.reason = answer.reason[:300]

        if row.needs_human:
            unanswerable.append(
                {
                    "external_id": question.external_id,
                    "text": question.text,
                    "required": question.required,
                    "reason": row.reason,
                }
            )
        else:
            resolved.append(
                {
                    "external_id": question.external_id,
                    "value": row.answer_value,
                    "type": question.type,
                    "required": question.required,
                }
            )
    if new_guard_flags:
        # Recorded on the application so the next policy evaluation sees them:
        # a fabricated answer must block auto-submission exactly like a
        # fabricated document does.
        application.fact_guard_flags = list(application.fact_guard_flags or []) + new_guard_flags
    db.flush()

    must_abort = any(item["required"] for item in unanswerable)
    return QuestionsOut(answers=resolved, unanswerable=unanswerable, must_abort=must_abort)


@router.post("/tasks/{application_id}/result", response_model=dict)
def report_result(
    application_id: uuid.UUID, payload: ResultIn, db: DbSession, _: AssistantAuth
) -> dict:
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    # Must be an attempt that is still OPEN. Taking the latest attempt whatever
    # its outcome let the same result be posted twice, which re-ran
    # record_submission and bumped the daily counter a second time.
    attempt = _open_attempt(db, application.id)
    if attempt is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No open attempt for this application")

    attempt.filled_fields = payload.filled_fields
    attempt.assistant_version = payload.assistant_version[:32]

    screenshot_id = None
    if payload.screenshot_base64:
        screenshot_id = _store_screenshot(db, application, payload.screenshot_base64)

    if payload.outcome == "submitted":
        workflow.record_submission(
            db,
            application,
            attempt,
            confirmation_number=payload.confirmation_number,
            receipt=payload.receipt,
            screenshot_document_id=screenshot_id,
        )
        return {"status": "recorded", "application_status": application.status}

    reason = ASSISTANT_ABORT_REASONS.get(payload.abort_reason, ReviewReason.SUBMISSION_ERROR.value)
    task = workflow.record_failure(
        db,
        application,
        attempt,
        error=payload.error_message
        or f"Assistant aborted: {payload.abort_reason or 'unspecified'}",
        guard_findings=payload.guard_findings if payload.outcome == "aborted" else None,
        review_reason=reason,
    )
    audit.record(
        db,
        AuditAction.POLICY_BLOCK
        if payload.outcome == "aborted"
        else AuditAction.APPLICATION_FAILED,
        user_id=application.user_id,
        actor="browser_assistant",
        object_type="application",
        object_id=str(application.id),
        outcome=payload.outcome,
        payload={"abort_reason": payload.abort_reason, "review_task_id": str(task.id)},
    )
    return {
        "status": "recorded",
        "application_status": application.status,
        "review_task_id": str(task.id),
        "review_reason": reason,
    }


def _store_screenshot(db, application: Application, encoded: str) -> uuid.UUID | None:
    import base64

    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        return None
    if len(content) > storage.MAX_UPLOAD_BYTES:
        return None
    digest = storage.sha256_of(content)
    key = storage.build_key(application.user_id, "screenshot", digest, "confirmation.png")
    storage.get_storage().write(key, content)
    document = Document(
        user_id=application.user_id,
        kind="other",
        label=f"Submission screenshot {application.id}",
        filename="confirmation.png",
        content_type="image/png",
        storage_key=key,
        size_bytes=len(content),
        sha256=digest,
        generated_for_job_id=application.job_id,
        generation_meta={"source": "browser_assistant"},
    )
    db.add(document)
    db.flush()
    return document.id
