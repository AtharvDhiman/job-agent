"""Draft -> validate -> gate -> (review | autofill | submit).

The gate is services/policy.decide(). This module never concludes on its own
that automation is allowed; it asks, then obeys, then records why.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import (
    ApplicationStatus,
    AuditAction,
    DocumentKind,
    NotificationKind,
    PipelineStage,
    QuestionType,
    ReviewReason,
    ReviewStatus,
    SubmissionPolicy,
)
from app.core.logging import get_logger
from app.models.application import (
    Application,
    ApplicationAnswer,
    ApplicationDocument,
    ReviewTask,
    SubmissionAttempt,
)
from app.models.audit import DailyCounter
from app.models.job import Job, JobMatch
from app.models.profile import CandidateProfile, CareerFact, Document
from app.models.user import AgentSettings, PlatformAuthorization, User
from app.services import answers as answers_service
from app.services import audit, document_generator, fact_guard, notifications, policy, storage
from app.utils.text import truncate

log = get_logger(__name__)

#: How many times the browser assistant may be handed the same application
#: before it is retired. Without a ceiling, a site that fails in a way a human
#: keeps re-approving becomes an unbounded stream of visits to that employer.
MAX_ATTEMPTS_PER_APPLICATION = 3

#: Statuses a human approval may move back into the assistant's queue. Anything
#: else is terminal for automation: an attempt that stopped on a hard stop is
#: never retried (browser-assistant NEVER_DO), one already sent is never sent
#: twice, and one being filled right now is not queued behind itself.
APPROVAL_MAY_REQUEUE = frozenset(
    {
        ApplicationStatus.DRAFTING.value,
        ApplicationStatus.NEEDS_REVIEW.value,
        ApplicationStatus.QUEUED.value,
        ApplicationStatus.APPROVED.value,
        ApplicationStatus.FAILED.value,
    }
)

#: The statuses GET /assistant/tasks/next selects from, and therefore the only
#: statuses claim_for_attempt() will move to IN_PROGRESS.
CLAIMABLE_STATUSES = (ApplicationStatus.QUEUED.value, ApplicationStatus.APPROVED.value)

#: Questions almost every ATS asks. The browser assistant replaces this with the
#: real form fields once it opens the page; this is only the drafting baseline.
BASELINE_QUESTIONS = [
    answers_service.Question(
        external_id="first_name",
        text="First name",
        type=QuestionType.SHORT_TEXT.value,
        required=True,
    ),
    answers_service.Question(
        external_id="last_name", text="Last name", type=QuestionType.SHORT_TEXT.value, required=True
    ),
    answers_service.Question(
        external_id="email", text="Email address", type=QuestionType.SHORT_TEXT.value, required=True
    ),
    answers_service.Question(
        external_id="phone", text="Phone number", type=QuestionType.SHORT_TEXT.value, required=False
    ),
    answers_service.Question(
        external_id="location",
        text="Current location (city)",
        type=QuestionType.SHORT_TEXT.value,
        required=False,
    ),
    answers_service.Question(
        external_id="linkedin",
        text="LinkedIn profile URL",
        type=QuestionType.SHORT_TEXT.value,
        required=False,
    ),
    answers_service.Question(
        external_id="website",
        text="Portfolio or website URL",
        type=QuestionType.SHORT_TEXT.value,
        required=False,
    ),
    answers_service.Question(
        external_id="work_auth",
        text="Are you legally authorized to work in the job location?",
        type=QuestionType.BOOLEAN.value,
        required=True,
        options=["Yes", "No"],
    ),
    answers_service.Question(
        external_id="sponsorship",
        text="Will you now or in the future require visa sponsorship?",
        type=QuestionType.BOOLEAN.value,
        required=True,
        options=["Yes", "No"],
    ),
]


@dataclass(slots=True)
class DraftResult:
    application: Application
    decision: policy.PolicyDecision
    review_task: ReviewTask | None = None
    documents: list[Document] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    blocking_questions: list[dict] = field(default_factory=list)


def today_key() -> str:
    return date.today().isoformat()


def applications_today(db: Session, user_id: uuid.UUID) -> int:
    counter = db.execute(
        select(DailyCounter).where(
            DailyCounter.user_id == user_id,
            DailyCounter.day == today_key(),
            DailyCounter.name == "applications_submitted",
        )
    ).scalar_one_or_none()
    return counter.value if counter else 0


def _counter_query(user_id: uuid.UUID, *, lock: bool):
    stmt = select(DailyCounter).where(
        DailyCounter.user_id == user_id,
        DailyCounter.day == today_key(),
        DailyCounter.name == "applications_submitted",
    )
    return stmt.with_for_update(nowait=False) if lock else stmt


def bump_applications_today(db: Session, user_id: uuid.UUID) -> int:
    """Increment today's submitted counter and return the new value.

    Two writers racing on the FIRST submission of the day both find no row and
    both insert. `ix_daily_counter` is UNIQUE on (user_id, day, name), so the
    loser gets an IntegrityError -- previously an unhandled 500 that also rolled
    back the submission that had just been recorded. Catch it in a SAVEPOINT and
    re-read the row the winner created, which is then locked FOR UPDATE like any
    other increment. Once the row exists the lock serialises every later bump, so
    the daily limit cannot be undercounted.
    """
    lock = db.bind is not None and db.bind.dialect.name == "postgresql"
    counter = db.execute(_counter_query(user_id, lock=lock)).scalar_one_or_none()

    if counter is None:
        try:
            with db.begin_nested():
                counter = DailyCounter(
                    user_id=user_id, day=today_key(), name="applications_submitted", value=0
                )
                db.add(counter)
                db.flush()
        except IntegrityError:
            counter = db.execute(_counter_query(user_id, lock=lock)).scalar_one_or_none()
            if counter is None:  # pragma: no cover - the row must exist by now
                raise

    counter.value += 1
    db.flush()
    return counter.value


def guard_answer(answer: answers_service.Answer, index: fact_guard.FactIndex) -> list[dict]:
    """Truthfulness check for one drafted screening answer.

    services/answers.py decides WHICH field an answer may be read from; this
    re-reads the produced TEXT and refuses anything that cannot be traced back
    to a verified fact or an explicit profile field. Documents were already
    re-checked this way; answers were not, which left the shorter and more
    load-bearing half of an application unguarded.

    A flagged answer is downgraded to needs_human, so it is neither auto-filled
    nor counted as answered, and its blocking flags are returned for the
    application's fact_guard_flags.
    """
    if answer.needs_human or not (answer.value or "").strip():
        return []
    report = fact_guard.check_answer(
        answer.value,
        index,
        source_fact_id=answer.source_fact_id,
        source_field=answer.source_field,
        question=answer.question.text,
    )
    blocking = [f.as_dict() for f in report.flags if f.severity == fact_guard.SEVERITY_BLOCK]
    if blocking:
        answer.needs_human = True
        answer.confidence = 0
        answer.reason = (
            "Fact guard rejected the drafted answer ("
            + ", ".join(sorted({f["kind"] for f in blocking}))
            + "). Answer this one yourself."
        )
    return blocking


def validate_preflight(
    application: Application,
    job: Job,
    profile: CandidateProfile,
    answer_rows: list[ApplicationAnswer],
    document_rows: list[ApplicationDocument],
) -> list[str]:
    """Everything that must be true before a form may be submitted."""
    errors: list[str] = []
    if not profile.full_name:
        errors.append("Profile is missing your full name")
    if not profile.contact_email:
        errors.append("Profile is missing a contact email")
    if not job.apply_url:
        errors.append("Job has no application URL")
    if not any(d.role == "resume" and d.attached for d in document_rows):
        errors.append("No resume attached")
    for answer in answer_rows:
        if answer.required and not (answer.answer_value or "").strip():
            errors.append(f"Required question unanswered: {truncate(answer.question_text, 80)}")
        if answer.required and answer.needs_human:
            errors.append(
                f"Required question needs your input: {truncate(answer.question_text, 80)}"
            )
    if (
        profile.min_salary_amount
        and job.salary_max
        and job.salary_currency == (profile.min_salary_currency or "")
    ):
        if job.salary_max < profile.min_salary_amount:
            errors.append("Advertised salary is below your stated minimum")
    if job.deadline_at:
        deadline = job.deadline_at
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        if deadline < datetime.now(UTC):
            errors.append("Application deadline has passed")
    return errors


def _store_generated(
    db: Session, user: User, job: Job, generated: document_generator.GeneratedDocument, kind: str
) -> Document:
    """Persist generated content, content-addressed.

    Identical output reuses the existing row rather than inserting a duplicate:
    re-drafting the same application, or two roles at the same company that
    produce the same tailored resume, must both be safe.
    """
    body = generated.body.encode("utf-8")
    digest = storage.sha256_of(body)

    existing = db.execute(
        select(Document).where(
            Document.user_id == user.id, Document.sha256 == digest, Document.kind == kind
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.generated_for_job_id = job.id
        existing.generation_meta = {**generated.meta, "guard": generated.guard}
        existing.label = generated.title
        db.flush()
        return existing

    key = storage.build_key(user.id, kind, digest, f"{kind}.md")
    storage.get_storage().write(key, body)
    document = Document(
        user_id=user.id,
        kind=kind,
        label=generated.title,
        filename=f"{kind}-{job.company_normalized or 'job'}.md",
        content_type="text/markdown",
        storage_key=key,
        size_bytes=len(body),
        sha256=digest,
        extracted_text=generated.body,
        generated_for_job_id=job.id,
        generation_meta={**generated.meta, "guard": generated.guard},
    )
    db.add(document)
    db.flush()
    audit.record(
        db,
        AuditAction.DOCUMENT_GENERATED,
        user_id=user.id,
        object_type="document",
        object_id=str(document.id),
        payload={"kind": kind, "job_id": str(job.id), "guard_blocked": generated.blocked},
    )
    return document


def draft_application(
    db: Session,
    user: User,
    job: Job,
    match: JobMatch | None = None,
    *,
    questions: list[answers_service.Question] | None = None,
    include_cover_letter: bool = True,
) -> DraftResult:
    profile = db.execute(
        select(CandidateProfile).where(CandidateProfile.user_id == user.id)
    ).scalar_one()
    agent_settings = db.execute(
        select(AgentSettings).where(AgentSettings.user_id == user.id)
    ).scalar_one()
    facts = list(
        db.execute(select(CareerFact).where(CareerFact.profile_id == profile.id)).scalars()
    )

    application = db.execute(
        select(Application).where(Application.user_id == user.id, Application.job_id == job.id)
    ).scalar_one_or_none()
    if application is None:
        application = Application(
            user_id=user.id,
            job_id=job.id,
            match_id=match.id if match else None,
            status=ApplicationStatus.DRAFTING.value,
            pipeline_stage=PipelineStage.SAVED.value,
        )
        db.add(application)
        db.flush()
    else:
        application.version += 1
        for row in list(application.documents) + list(application.answers):
            db.delete(row)
        db.flush()

    # --- documents -------------------------------------------------------
    generated_docs: list[Document] = []
    guard_flags: list[dict] = []

    resume = document_generator.generate_resume(profile, facts, job)
    guard_flags += resume.guard.get("flags", [])
    resume_doc = _store_generated(db, user, job, resume, DocumentKind.RESUME_GENERATED.value)
    generated_docs.append(resume_doc)
    db.add(
        ApplicationDocument(application_id=application.id, document_id=resume_doc.id, role="resume")
    )

    if include_cover_letter:
        letter = document_generator.generate_cover_letter(profile, facts, job)
        guard_flags += letter.guard.get("flags", [])
        letter_doc = _store_generated(
            db, user, job, letter, DocumentKind.COVER_LETTER_GENERATED.value
        )
        generated_docs.append(letter_doc)
        db.add(
            ApplicationDocument(
                application_id=application.id, document_id=letter_doc.id, role="cover_letter"
            )
        )

    # --- answers ---------------------------------------------------------
    question_list = questions if questions is not None else BASELINE_QUESTIONS
    drafted = answers_service.answer_all(question_list, profile, facts)
    answer_index = fact_guard.FactIndex(profile, facts)
    for answer in drafted:
        guard_flags += guard_answer(answer, answer_index)
    answer_rows: list[ApplicationAnswer] = []
    for answer in drafted:
        row = ApplicationAnswer(
            application_id=application.id,
            question_external_id=answer.question.external_id,
            question_text=answer.question.text,
            question_type=answer.question.type,
            required=answer.question.required,
            options=answer.question.options,
            answer_value=answer.value,
            source_fact_id=uuid.UUID(answer.source_fact_id) if answer.source_fact_id else None,
            confidence=answer.confidence,
            needs_human=answer.needs_human,
            reason=answer.reason[:300],
        )
        db.add(row)
        answer_rows.append(row)
    db.flush()

    document_rows = list(
        db.execute(
            select(ApplicationDocument).where(ApplicationDocument.application_id == application.id)
        ).scalars()
    )
    validation_errors = validate_preflight(application, job, profile, answer_rows, document_rows)
    blocking = [
        {"question": a.question.text, "reason": a.reason, "type": a.question.type}
        for a in drafted
        if a.needs_human and a.question.required
    ]
    guard_blocked = any(f.get("severity") == "block" for f in guard_flags)

    # --- policy gate -----------------------------------------------------
    authorization = db.execute(
        select(PlatformAuthorization).where(
            PlatformAuthorization.user_id == user.id,
            PlatformAuthorization.platform_key == job.connector_key,
        )
    ).scalar_one_or_none()
    decision = policy.decide(
        job=job,
        connector_policy=job.submission_policy_default,
        authorization=authorization,
        agent_settings=agent_settings,
        score=match.score if match else 0,
        global_enabled=settings.automation_global_enabled,
        applications_today=applications_today(db, user.id),
        fact_guard_blocked=guard_blocked,
        blocking_questions=len(blocking),
        validation_errors=len(validation_errors),
    )

    application.submission_policy = decision.policy
    application.fact_guard_flags = guard_flags
    application.validation_errors = validation_errors
    application.summary = _summary(job, match, decision, generated_docs, blocking)
    application.prefilled_fields = {
        a.question_external_id: a.answer_value
        for a in answer_rows
        if a.answer_value and not a.needs_human
    }

    review_task = None
    explicit_autofill = (
        decision.may_autofill
        and decision.granted_policy == SubmissionPolicy.ASSISTED_AUTOFILL.value
    )
    if decision.may_submit:
        application.status = ApplicationStatus.QUEUED.value
    elif explicit_autofill:
        # You asked for the form to be filled but not sent. Queue it for the
        # assistant AND open a review task so you know it is waiting on your click.
        application.status = ApplicationStatus.QUEUED.value
        review_task = _open_review(
            db, user, job, application, decision, blocking, validation_errors
        )
    else:
        application.status = ApplicationStatus.NEEDS_REVIEW.value
        review_task = _open_review(
            db, user, job, application, decision, blocking, validation_errors
        )

    db.flush()
    audit.record(
        db,
        AuditAction.APPLICATION_DRAFTED,
        user_id=user.id,
        object_type="application",
        object_id=str(application.id),
        payload={
            "job_id": str(job.id),
            "score": match.score if match else None,
            "decision": decision.as_dict(),
            "guard_flags": len(guard_flags),
            "validation_errors": validation_errors,
        },
    )
    return DraftResult(
        application=application,
        decision=decision,
        review_task=review_task,
        documents=generated_docs,
        validation_errors=validation_errors,
        blocking_questions=blocking,
    )


def _summary(
    job: Job,
    match: JobMatch | None,
    decision: policy.PolicyDecision,
    documents: list[Document],
    blocking: list[dict],
) -> str:
    lines = [
        f"{job.title} at {job.company} ({job.location_raw or 'location not stated'})",
        f"Source: {job.connector_key} | {job.apply_url}",
        f"Match score: {match.score if match else 'n/a'}",
        f"Decision: {decision.policy}"
        + (" (auto-submit)" if decision.may_submit else " (queued for you)"),
        "Why: " + " ".join(decision.rationale),
        "Documents: " + ", ".join(d.label for d in documents),
    ]
    if blocking:
        lines.append(
            f"Needs your answer ({len(blocking)}): " + "; ".join(b["question"] for b in blocking)
        )
    return "\n".join(lines)


def _open_review(
    db: Session,
    user: User,
    job: Job,
    application: Application,
    decision: policy.PolicyDecision,
    blocking: list[dict],
    validation_errors: list[str],
) -> ReviewTask:
    reason = (
        decision.review_reasons[0] if decision.review_reasons else ReviewReason.MANUAL_REQUEST.value
    )
    task = ReviewTask(
        user_id=user.id,
        application_id=application.id,
        job_id=job.id,
        reason=reason,
        status=ReviewStatus.OPEN.value,
        title=f"Review: {job.title} at {job.company}",
        detail="\n".join(decision.rationale + validation_errors),
        action_url=job.apply_url or job.source_url,
        draft_payload={
            "summary": application.summary,
            "prefilled_fields": application.prefilled_fields,
            "policy": decision.as_dict(),
            "validation_errors": validation_errors,
        },
        blocking_questions=blocking,
    )
    db.add(task)
    db.flush()
    audit.record(
        db,
        AuditAction.REVIEW_CREATED,
        user_id=user.id,
        object_type="review_task",
        object_id=str(task.id),
        payload={"reason": reason, "application_id": str(application.id)},
    )
    notifications.create(
        db,
        user_id=user.id,
        kind=NotificationKind.REVIEW_REQUIRED,
        title=task.title,
        body=task.detail or "An application is waiting for your review.",
        link=f"/reviews/{task.id}",
        data={"reason": reason, "application_id": str(application.id)},
    )
    return task


def approve(db: Session, application: Application, user: User, note: str = "") -> Application:
    """Human approval: resolve the open reviews, and re-queue if that is allowed.

    Approving is how a review task is closed, so it must work for every review --
    including the one that exists because the platform may never be automated at
    all. What it must NOT do is turn every such acknowledgement into a fresh
    automation job: an application that stopped on a hard stop, was already sent,
    or is being filled right now keeps its status and stays out of the queue.
    Note that APPROVED status alone never authorises anything either; the gate is
    re-run at hand-out time by policy.may_hand_out().
    """
    requeued = application.status in APPROVAL_MAY_REQUEUE
    if requeued:
        application.status = ApplicationStatus.APPROVED.value
        application.approved_by_user_id = user.id
        application.approved_at = datetime.now(UTC)
    for task in application.reviews:
        if task.status == ReviewStatus.OPEN.value:
            task.status = ReviewStatus.APPROVED.value
            task.resolved_at = datetime.now(UTC)
            task.resolution_note = note[:1000]
    db.flush()
    audit.record(
        db,
        AuditAction.APPLICATION_APPROVED,
        user_id=user.id,
        actor=user.email,
        object_type="application",
        object_id=str(application.id),
        payload={"note": note[:300], "requeued": requeued, "status": application.status},
    )
    return application


def reject(db: Session, application: Application, user: User, note: str = "") -> Application:
    application.status = ApplicationStatus.CANCELLED.value
    application.pipeline_stage = PipelineStage.CLOSED.value
    for task in application.reviews:
        if task.status == ReviewStatus.OPEN.value:
            task.status = ReviewStatus.REJECTED.value
            task.resolved_at = datetime.now(UTC)
            task.resolution_note = note[:1000]
    db.flush()
    audit.record(
        db,
        AuditAction.APPLICATION_REJECTED,
        user_id=user.id,
        actor=user.email,
        object_type="application",
        object_id=str(application.id),
        payload={"note": note[:300]},
    )
    return application


def claim_for_attempt(db: Session, application: Application, mode: str) -> SubmissionAttempt | None:
    """Take exclusive ownership of an application and open one attempt on it.

    The status change is a conditional UPDATE rather than an assignment, so two
    assistants polling the same queue in the same instant cannot both walk away
    with the same application: exactly one of them changes a row, the other gets
    rowcount 0 and moves on. Returns None when the claim was lost or the attempt
    ceiling has been reached.
    """
    if application.attempt_count >= MAX_ATTEMPTS_PER_APPLICATION:
        return None
    result = db.execute(
        update(Application)
        .where(
            Application.id == application.id,
            Application.status.in_(CLAIMABLE_STATUSES),
            Application.attempt_count < MAX_ATTEMPTS_PER_APPLICATION,
        )
        .values(
            status=ApplicationStatus.IN_PROGRESS.value,
            attempt_count=Application.attempt_count + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        return None
    db.refresh(application)
    attempt = SubmissionAttempt(
        application_id=application.id,
        attempt_number=application.attempt_count,
        mode=mode,
        outcome="pending",
        started_at=datetime.now(UTC),
    )
    db.add(attempt)
    db.flush()
    return attempt


def retire_exhausted(db: Session, application: Application) -> ReviewTask:
    """Take an application out of the queue for good after too many attempts."""
    error = (
        f"Stopped after {application.attempt_count} attempts. The agent does not keep "
        "visiting the same employer; finish this one yourself using the link below."
    )
    application.status = ApplicationStatus.FAILED.value
    application.last_error = error
    job = db.get(Job, application.job_id)
    task = ReviewTask(
        user_id=application.user_id,
        application_id=application.id,
        job_id=application.job_id,
        reason=ReviewReason.SUBMISSION_ERROR.value,
        status=ReviewStatus.OPEN.value,
        title=f"Attempt limit reached: {job.title if job else 'application'}",
        detail=error,
        action_url=(job.apply_url or job.source_url) if job else "",
        draft_payload={"prefilled_fields": application.prefilled_fields},
    )
    db.add(task)
    db.flush()
    audit.record(
        db,
        AuditAction.POLICY_BLOCK,
        user_id=application.user_id,
        object_type="application",
        object_id=str(application.id),
        outcome="blocked",
        payload={"reason": "attempt_limit_reached", "attempts": application.attempt_count},
    )
    return task


def record_human_submission(
    db: Session,
    application: Application,
    user: User,
    *,
    confirmation_number: str = "",
    note: str = "",
) -> Application:
    """You clicked submit yourself; tell the system so it stops tracking it.

    This is the other half of the assisted-autofill hand-off: the assistant fills
    the form, refuses to report a submission it did not make, and you close the
    loop here. The attempt is recorded as submitted_by_human so the audit trail
    never claims the agent sent something a person sent.
    """
    if application.status == ApplicationStatus.SUBMITTED.value:
        raise ValueError("This application is already recorded as submitted.")
    now = datetime.now(UTC)
    attempt = db.execute(
        select(SubmissionAttempt)
        .where(SubmissionAttempt.application_id == application.id)
        .order_by(SubmissionAttempt.attempt_number.desc())
        .limit(1)
    ).scalar_one_or_none()
    if attempt is None or attempt.outcome != "pending":
        application.attempt_count += 1
        attempt = SubmissionAttempt(
            application_id=application.id,
            attempt_number=application.attempt_count,
            mode="manual",
            started_at=now,
        )
        db.add(attempt)
    attempt.outcome = "submitted_by_human"
    attempt.finished_at = now

    application.status = ApplicationStatus.SUBMITTED.value
    application.pipeline_stage = PipelineStage.APPLIED.value
    application.submitted_at = now
    application.confirmation_number = confirmation_number or None
    application.submission_receipt = {
        "reported_by": "account_owner",
        "note": note[:500],
        "submitted_at": now.isoformat(),
    }
    for task in application.reviews:
        if task.status == ReviewStatus.OPEN.value:
            task.status = ReviewStatus.APPROVED.value
            task.resolved_at = now
            task.resolution_note = (note or "Submitted by hand.")[:1000]
    bump_applications_today(db, application.user_id)
    db.flush()
    audit.record(
        db,
        AuditAction.APPLICATION_SUBMITTED,
        user_id=application.user_id,
        actor=user.email,
        object_type="application",
        object_id=str(application.id),
        payload={
            "attempt": attempt.attempt_number,
            "mode": "manual",
            "confirmation_present": bool(confirmation_number),
        },
    )
    return application


def record_submission(
    db: Session,
    application: Application,
    attempt: SubmissionAttempt,
    *,
    confirmation_number: str = "",
    receipt: dict | None = None,
    screenshot_document_id: uuid.UUID | None = None,
) -> Application:
    if attempt.outcome != "pending" or application.status != ApplicationStatus.IN_PROGRESS.value:
        raise ValueError(
            "Only an application with an open attempt can be recorded as submitted "
            f"(status={application.status}, attempt={attempt.outcome})."
        )
    now = datetime.now(UTC)
    attempt.outcome = "submitted"
    attempt.finished_at = now
    attempt.screenshot_document_id = screenshot_document_id
    application.status = ApplicationStatus.SUBMITTED.value
    application.pipeline_stage = PipelineStage.APPLIED.value
    application.submitted_at = now
    application.confirmation_number = confirmation_number or None
    application.submission_receipt = receipt or {}
    application.screenshot_document_id = screenshot_document_id
    bump_applications_today(db, application.user_id)
    db.flush()
    audit.record(
        db,
        AuditAction.APPLICATION_SUBMITTED,
        user_id=application.user_id,
        actor="browser_assistant",
        object_type="application",
        object_id=str(application.id),
        payload={
            "attempt": attempt.attempt_number,
            "mode": attempt.mode,
            "confirmation_present": bool(confirmation_number),
        },
    )
    job = db.get(Job, application.job_id)
    notifications.create(
        db,
        user_id=application.user_id,
        kind=NotificationKind.SUBMISSION_SUCCEEDED,
        title=f"Submitted: {job.title if job else 'application'}",
        # The confirmation number is an EncryptedString on `applications`. Copying
        # its value into `notifications.body` -- plain text, and emailed when the
        # email channel is on -- put it straight back in the clear. Say whether
        # there is one; the value itself stays behind the authenticated detail view.
        body=(
            "The site returned a confirmation number; open the application to see it."
            if confirmation_number
            else "The site did not return a confirmation number."
        ),
        link=f"/applications/{application.id}",
    )
    return application


def record_failure(
    db: Session,
    application: Application,
    attempt: SubmissionAttempt,
    *,
    error: str,
    guard_findings: list[dict] | None = None,
    review_reason: str = ReviewReason.SUBMISSION_ERROR.value,
) -> ReviewTask:
    """A failed or aborted attempt always ends as a review task, never a retry loop."""
    now = datetime.now(UTC)
    attempt.outcome = "aborted" if guard_findings else "failed"
    attempt.finished_at = now
    attempt.error_message = error[:2000]
    attempt.guard_findings = guard_findings or []
    application.status = (
        ApplicationStatus.BLOCKED_BY_POLICY.value
        if guard_findings
        else ApplicationStatus.FAILED.value
    )
    application.last_error = error[:2000]

    job = db.get(Job, application.job_id)
    task = ReviewTask(
        user_id=application.user_id,
        application_id=application.id,
        job_id=application.job_id,
        reason=review_reason,
        status=ReviewStatus.OPEN.value,
        title=f"Could not submit: {job.title if job else 'application'}",
        detail=error[:4000],
        action_url=(job.apply_url or job.source_url) if job else "",
        draft_payload={"prefilled_fields": application.prefilled_fields},
        blocking_questions=guard_findings or [],
    )
    db.add(task)
    db.flush()
    audit.record(
        db,
        AuditAction.APPLICATION_FAILED,
        user_id=application.user_id,
        actor="browser_assistant",
        object_type="application",
        object_id=str(application.id),
        outcome="failed",
        payload={"error": error[:500], "review_reason": review_reason},
    )
    notifications.create(
        db,
        user_id=application.user_id,
        kind=NotificationKind.SUBMISSION_FAILED,
        title=task.title,
        body=error[:1000],
        link=f"/reviews/{task.id}",
    )
    return task


def pipeline_counts(db: Session, user_id: uuid.UUID) -> dict[str, int]:
    rows = db.execute(
        select(Application.pipeline_stage, func.count(Application.id))
        .where(Application.user_id == user_id)
        .group_by(Application.pipeline_stage)
    ).all()
    counts = {stage.value: 0 for stage in PipelineStage}
    for stage, total in rows:
        counts[stage] = int(total)
    return counts
