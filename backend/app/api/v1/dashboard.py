from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, get_agent_settings
from app.core.config import settings as app_settings
from app.core.enums import (
    ApplicationStatus,
    DocumentKind,
    MatchDecision,
    ReviewReason,
    ReviewStatus,
)
from app.models.application import Application, ReviewTask
from app.models.audit import AuditLog
from app.models.job import Job, JobMatch, JobSourceSubscription
from app.models.profile import CandidateProfile, CareerFact, Document
from app.models.user import AgentSettings, User
from app.schemas.settings import DashboardOut
from app.services import application_workflow as workflow
from app.services import llm, notifications

router = APIRouter(tags=["dashboard"])

#: Applications that stopped short of a submission. Grouped into one bucket
#: because from the user's side they are the same question -- "why did this not
#: go out?" -- and the answer comes from the attached ReviewTask, not the status.
_STOPPED_STATUSES = (
    ApplicationStatus.FAILED.value,
    ApplicationStatus.BLOCKED_BY_POLICY.value,
    ApplicationStatus.CANCELLED.value,
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


def _empty_state(db: DbSession, user: User, match_count: int) -> dict:
    """Explain an empty dashboard in terms of the switch the user has to flip."""
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


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(
    db: DbSession,
    user: CurrentUser,
    hours: int = Query(default=24, ge=1, le=720),
    agent_settings: AgentSettings = Depends(get_agent_settings),
) -> DashboardOut:
    since = datetime.now(UTC) - timedelta(hours=hours)

    def count(model, *conditions) -> int:
        return int(
            db.execute(
                select(func.count(model.id)).where(model.user_id == user.id, *conditions)
            ).scalar_one()
        )

    top_rows = db.execute(
        select(JobMatch, Job)
        .join(Job, Job.id == JobMatch.job_id)
        .where(
            JobMatch.user_id == user.id,
            JobMatch.decision == MatchDecision.SHORTLISTED.value,
            JobMatch.dismissed_at.is_(None),
        )
        .order_by(JobMatch.score.desc(), Job.posted_at.desc().nullslast())
        .limit(10)
    ).all()

    reason_rows = db.execute(
        select(JobMatch.decision, func.count(JobMatch.id))
        .where(
            JobMatch.user_id == user.id,
            JobMatch.created_at >= since,
            JobMatch.decision != MatchDecision.SHORTLISTED.value,
        )
        .group_by(JobMatch.decision)
        .order_by(func.count(JobMatch.id).desc())
    ).all()

    activity = db.execute(
        select(AuditLog)
        .where(AuditLog.user_id == user.id, AuditLog.created_at >= since)
        .order_by(AuditLog.seq.desc())
        .limit(25)
    ).scalars()

    # --- buckets: "what is the agent doing, and what is blocking it" ---------
    # Grouped by the ReviewTask attached to a stopped application, because the
    # status alone ("failed") never says why; the reason does.
    failure_reason_rows = db.execute(
        select(ReviewTask.reason, func.count(ReviewTask.id))
        .join(Application, Application.id == ReviewTask.application_id)
        .where(
            ReviewTask.user_id == user.id,
            Application.status.in_(_STOPPED_STATUSES),
        )
        .group_by(ReviewTask.reason)
        .order_by(func.count(ReviewTask.id).desc())
    ).all()

    buckets = {
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
            "count": count(Application, Application.status.in_(_STOPPED_STATUSES)),
            "link": "/applications",
            "failure_reasons": [
                {"reason": reason, "count": int(total), "label": reason_label(reason)}
                for reason, total in failure_reason_rows
            ],
        },
    }

    return DashboardOut(
        automation_enabled=agent_settings.automation_enabled,
        global_automation_enabled=app_settings.automation_global_enabled,
        paused_reason=agent_settings.paused_reason,
        applications_today=workflow.applications_today(db, user.id),
        daily_application_limit=agent_settings.daily_application_limit,
        auto_submit_min_score=agent_settings.auto_submit_min_score,
        new_matches=count(JobMatch, JobMatch.created_at >= since),
        shortlisted=count(
            JobMatch,
            JobMatch.created_at >= since,
            JobMatch.decision == MatchDecision.SHORTLISTED.value,
        ),
        awaiting_review=count(ReviewTask, ReviewTask.status == ReviewStatus.OPEN.value),
        auto_submitted=count(
            Application,
            Application.status == ApplicationStatus.SUBMITTED.value,
            Application.approved_by_user_id.is_(None),
            Application.submitted_at >= since,
        ),
        rejected_or_skipped=count(
            JobMatch,
            JobMatch.created_at >= since,
            JobMatch.decision != MatchDecision.SHORTLISTED.value,
        ),
        pipeline=workflow.pipeline_counts(db, user.id),
        unread_notifications=notifications.unread_count(db, user.id),
        llm_mode="claude" if llm.is_enabled() else "deterministic",
        top_matches=[
            {
                "match_id": str(match.id),
                "job_id": str(job.id),
                "score": match.score,
                "title": job.title,
                "company": job.company,
                "location": job.location_raw,
                "connector": job.connector_key,
                "posted_at": job.posted_at.isoformat() if job.posted_at else None,
                "direct": job.is_direct_employer,
                "matching_skills": match.matching_skills[:8],
                "missing_skills": match.missing_skills[:8],
                "risks": match.risks[:4],
            }
            for match, job in top_rows
        ],
        rejection_reasons=[
            {"decision": decision, "count": int(total)} for decision, total in reason_rows
        ],
        buckets=buckets,
        empty_state=_empty_state(db, user, count(JobMatch)),
        recent_activity=[
            {
                "seq": entry.seq,
                "at": entry.created_at.isoformat(),
                "actor": entry.actor,
                "action": entry.action,
                "object_type": entry.object_type,
                "object_id": entry.object_id,
                "outcome": entry.outcome,
            }
            for entry in activity
        ],
    )
