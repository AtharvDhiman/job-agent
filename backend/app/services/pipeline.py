"""The pipeline's own vocabulary: six buckets, and the words for why work stopped.

Every front door leads with these numbers. `GET /dashboard` returns them,
`python -m app.cli status` prints them, and services/html_report renders them
into a file the user opens with no server running. They used to live inside the
dashboard route function, which left the other two front doors with only bad
options: import a router from a service (a cycle waiting to be created the
first time a router renders a report), or write the predicates again -- which
is what happened, and the copies had already drifted apart on a label.

None of this is about HTTP. What counts as "needs review" is a fact about the
pipeline, so it lives in a service and the route reads it like everyone else.
Add a seventh bucket here and all three front doors gain it at once; there is
nowhere else to add one.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.enums import (
    ApplicationStatus,
    DocumentKind,
    MatchDecision,
    ReviewReason,
    ReviewStatus,
)
from app.models.application import Application, ReviewTask
from app.models.job import JobMatch, JobSourceSubscription
from app.models.profile import CandidateProfile, CareerFact, Document
from app.models.user import AgentSettings, User

#: Applications that stopped short of a submission. Grouped into one bucket
#: because from the user's side they are the same question -- "why did this not
#: go out?" -- and the answer comes from the attached ReviewTask, not the status.
STOPPED_STATUSES: tuple[str, ...] = (
    ApplicationStatus.FAILED.value,
    ApplicationStatus.BLOCKED_BY_POLICY.value,
    ApplicationStatus.CANCELLED.value,
)

#: The six buckets in the order a human reads them -- what arrived, what is
#: good, what is moving, what is stuck, what went out, what died -- each with
#: the label and the sentence that says what it actually counts. Every renderer
#: takes its wording from here, so the terminal and the report cannot disagree
#: about what a bucket is called.
BUCKET_LABELS: tuple[tuple[str, str, str], ...] = (
    ("new_jobs_found", "New jobs found", "Scored against your profile in this window."),
    ("high_match", "High match", "Shortlisted and at or above your auto-submit score."),
    (
        "queued_for_auto",
        "Queued for auto-submit",
        "Approved or queued, waiting for a submission run.",
    ),
    ("needs_review", "Needs review", "Open review tasks waiting on you."),
    ("submitted", "Submitted", "Applications sent in this window."),
    ("failed_or_stopped", "Failed or stopped", "Started but did not go out. Reasons below."),
)

#: Short, plain-language rendering of every ReviewReason. The enum value is the
#: stable identifier the UI filters on; this is only what a human reads.
REASON_LABELS: dict[str, str] = {
    ReviewReason.BELOW_AUTO_SUBMIT_THRESHOLD.value: "Score below your threshold",
    ReviewReason.PLATFORM_NOT_AUTHORIZED.value: "Platform not authorized",
    ReviewReason.PLATFORM_PROHIBITS_AUTOMATION.value: "Platform prohibits automation",
    ReviewReason.UNSUPPORTED_PLATFORM.value: "No supported apply flow",
    ReviewReason.CAPTCHA_DETECTED.value: "CAPTCHA on the form",
    ReviewReason.LOGIN_REQUIRED.value: "Sign-in required",
    ReviewReason.BOT_PROTECTION_DETECTED.value: "Bot protection on the page",
    ReviewReason.ROBOTS_DISALLOWED.value: "robots.txt disallows this page",
    ReviewReason.UNANSWERABLE_QUESTION.value: "Question needs a verified fact",
    ReviewReason.FREE_TEXT_QUESTION.value: "Free-text question needs you",
    ReviewReason.MISSING_VERIFIED_FACT.value: "Missing a verified fact",
    ReviewReason.FACT_GUARD_FLAGGED.value: "Unverified claim in the draft",
    ReviewReason.VALIDATION_FAILED.value: "Pre-flight validation failed",
    ReviewReason.MISSING_ATTACHMENT.value: "Required attachment missing",
    ReviewReason.DAILY_LIMIT_REACHED.value: "Daily limit reached",
    ReviewReason.AUTOMATION_DISABLED.value: "Automation is paused",
    ReviewReason.SUBMISSION_ERROR.value: "Submission error",
    ReviewReason.MANUAL_REQUEST.value: "Waiting for your approval",
}


def reason_label(reason: str) -> str:
    """Never invent a label: an unknown reason is shown as its own identifier."""
    return REASON_LABELS.get(reason, reason.replace("_", " ").capitalize())


def buckets(
    db: Session, user: User, agent_settings: AgentSettings, since: datetime
) -> dict[str, dict]:
    """The six buckets: how many, where to look, and why things stopped.

    Read-only. `link` is a frontend route, which the offline report ignores and
    the API returns verbatim. `failure_reasons` is grouped by the ReviewTask
    attached to a stopped application, because the status alone ("failed")
    never says why.
    """

    def count(model, *conditions) -> int:
        return int(
            db.execute(
                select(func.count(model.id)).where(model.user_id == user.id, *conditions)
            ).scalar_one()
        )

    failure_reason_rows = db.execute(
        select(ReviewTask.reason, func.count(ReviewTask.id))
        .join(Application, Application.id == ReviewTask.application_id)
        .where(
            ReviewTask.user_id == user.id,
            Application.status.in_(STOPPED_STATUSES),
        )
        .group_by(ReviewTask.reason)
        .order_by(func.count(ReviewTask.id).desc())
    ).all()

    return {
        "new_jobs_found": {
            "count": count(JobMatch, JobMatch.created_at >= since),
            "link": "/jobs",
        },
        "high_match": {
            "count": count(
                JobMatch,
                JobMatch.decision == MatchDecision.SHORTLISTED.value,
                JobMatch.dismissed_at.is_(None),
                JobMatch.score >= agent_settings.auto_submit_min_score,
            ),
            "link": f"/jobs?min_score={agent_settings.auto_submit_min_score}",
        },
        "queued_for_auto": {
            "count": count(
                Application,
                Application.status.in_(
                    (ApplicationStatus.QUEUED.value, ApplicationStatus.APPROVED.value)
                ),
            ),
            "link": "/applications",
        },
        "needs_review": {
            "count": count(ReviewTask, ReviewTask.status == ReviewStatus.OPEN.value),
            "link": "/reviews",
        },
        "submitted": {
            "count": count(
                Application,
                Application.status == ApplicationStatus.SUBMITTED.value,
                Application.submitted_at >= since,
            ),
            "link": "/applications",
        },
        "failed_or_stopped": {
            "count": count(Application, Application.status.in_(STOPPED_STATUSES)),
            "link": "/applications",
            "failure_reasons": [
                {"reason": reason, "count": int(total), "label": reason_label(reason)}
                for reason, total in failure_reason_rows
            ],
        },
    }


def empty_state(db: Session, user: User, match_count: int) -> dict:
    """Explain an empty pipeline in terms of the switch the user has to flip."""
    profile_id = db.execute(
        select(CandidateProfile.id).where(CandidateProfile.user_id == user.id)
    ).scalar_one_or_none()
    has_verified_facts = profile_id is not None and (
        db.execute(
            select(CareerFact.id)
            .where(CareerFact.profile_id == profile_id, CareerFact.verified.is_(True))
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )
    has_resume = (
        db.execute(
            select(Document.id)
            .where(Document.user_id == user.id, Document.kind == DocumentKind.RESUME_SOURCE.value)
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )
    has_sources = (
        db.execute(
            select(JobSourceSubscription.id)
            .where(JobSourceSubscription.user_id == user.id)
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )
    is_empty = match_count == 0

    if not has_resume:
        next_step = "Upload a resume on the Profile page."
    elif not has_verified_facts:
        next_step = "Verify your proposed career facts on the Profile page."
    elif not has_sources:
        next_step = "Add a job source under Settings."
    elif is_empty:
        next_step = "Run Autopilot to discover and score jobs."
    else:
        next_step = "Review your shortlist and approve what you want submitted."

    if not is_empty:
        message = f"The agent has scored {match_count} job(s) against your profile."
    elif not has_sources:
        message = "No jobs yet: the agent has no source to poll."
    elif not (has_resume and has_verified_facts):
        message = "No jobs yet: the agent needs a resume and verified facts before it scores."
    else:
        message = "No jobs scored yet. Sources are configured but discovery has not run."

    return {
        "is_empty": is_empty,
        "has_sources": has_sources,
        "has_verified_facts": has_verified_facts,
        "has_resume": has_resume,
        "message": message,
        "next_step": next_step,
    }
