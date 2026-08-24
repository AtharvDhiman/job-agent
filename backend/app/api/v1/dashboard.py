from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, get_agent_settings
from app.core.config import settings as app_settings
from app.core.enums import ApplicationStatus, MatchDecision, ReviewStatus
from app.models.application import Application, ReviewTask
from app.models.audit import AuditLog
from app.models.job import Job, JobMatch
from app.models.user import AgentSettings
from app.schemas.settings import DashboardOut
from app.services import application_workflow as workflow
from app.services import llm, notifications

router = APIRouter(tags=["dashboard"])


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
