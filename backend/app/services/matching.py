"""Score newly discovered jobs for a user and persist the explanation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import AuditAction, DocumentKind, MatchDecision, NotificationKind
from app.core.logging import get_logger
from app.models.job import Job, JobMatch
from app.models.profile import CandidateProfile, CareerFact, Document
from app.models.user import AgentSettings, User
from app.services import audit, notifications
from app.services.ranking import SemanticIndex, score_job

log = get_logger(__name__)

HIGH_MATCH_NOTIFY_SCORE = 80


def _resume_text(db: Session, user_id: uuid.UUID) -> tuple[str, uuid.UUID | None]:
    """Primary resume first, else the most recent uploaded one."""
    stmt = (
        select(Document)
        .where(Document.user_id == user_id, Document.kind == DocumentKind.RESUME_SOURCE.value)
        .order_by(Document.is_primary.desc(), Document.created_at.desc())
        .limit(1)
    )
    document = db.execute(stmt).scalar_one_or_none()
    return ((document.extracted_text or "") if document else ""), (
        document.id if document else None
    )


def candidate_jobs(db: Session, user_id: uuid.UUID, *, max_age_hours: int, limit: int = 500):
    """Unscored, non-duplicate jobs recent enough to be worth evaluating."""
    cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours * 2)
    scored = select(JobMatch.job_id).where(JobMatch.user_id == user_id)
    stmt = (
        select(Job)
        .where(
            Job.canonical_job_id.is_(None),
            Job.closed_at.is_(None),
            Job.last_seen_at >= cutoff,
            Job.id.not_in(scored),
        )
        .order_by(Job.posted_at.desc().nullslast(), Job.first_seen_at.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars())


def score_for_user(db: Session, user: User, *, limit: int = 500, notify: bool = True) -> dict:
    profile = db.execute(
        select(CandidateProfile).where(CandidateProfile.user_id == user.id)
    ).scalar_one_or_none()
    agent_settings = db.execute(
        select(AgentSettings).where(AgentSettings.user_id == user.id)
    ).scalar_one_or_none()
    if profile is None or agent_settings is None:
        return {
            "scored": 0,
            "shortlisted": 0,
            "rejected": 0,
            "error": "profile_or_settings_missing",
        }

    jobs = candidate_jobs(db, user.id, max_age_hours=agent_settings.job_max_age_hours, limit=limit)
    if not jobs:
        return {"scored": 0, "shortlisted": 0, "rejected": 0}

    resume_text, resume_id = _resume_text(db, user.id)
    facts = list(
        db.execute(select(CareerFact).where(CareerFact.profile_id == profile.id)).scalars()
    )
    verified_text = " ".join(
        f"{f.title} {f.organization} {f.value} {' '.join(f.highlights or [])}"
        for f in facts
        if f.verified
    )
    index = SemanticIndex([j.description_text for j in jobs])

    shortlisted = rejected = 0
    high_matches: list[tuple[Job, int]] = []
    for job in jobs:
        breakdown = score_job(
            job,
            profile,
            resume_text=f"{resume_text}\n{verified_text}",
            index=index,
            max_age_hours=agent_settings.job_max_age_hours,
            shortlist_min_score=agent_settings.shortlist_min_score,
        )
        match = JobMatch(
            user_id=user.id,
            job_id=job.id,
            score=breakdown.score,
            decision=breakdown.decision,
            component_scores=breakdown.components,
            matching_skills=breakdown.matching_skills,
            missing_skills=breakdown.missing_skills,
            risks=breakdown.risks,
            hard_filter_failures=breakdown.hard_filter_failures,
            explanation=breakdown.explanation,
            semantic_similarity=breakdown.semantic_similarity,
            scored_by="deterministic",
            recommended_resume_id=resume_id,
        )
        db.add(match)
        if breakdown.decision == MatchDecision.SHORTLISTED.value:
            shortlisted += 1
            if breakdown.score >= HIGH_MATCH_NOTIFY_SCORE:
                high_matches.append((job, breakdown.score))
        else:
            rejected += 1

    db.flush()
    audit.record(
        db,
        AuditAction.MATCH_SCORED,
        user_id=user.id,
        object_type="job_match",
        payload={"scored": len(jobs), "shortlisted": shortlisted, "rejected": rejected},
    )

    if notify and high_matches:
        top = sorted(high_matches, key=lambda pair: -pair[1])[:10]
        notifications.create(
            db,
            user_id=user.id,
            kind=NotificationKind.HIGH_MATCH_JOB,
            title=f"{len(high_matches)} high-match job(s) found",
            body="\n".join(f"{score}: {job.title} at {job.company}" for job, score in top),
            link="/jobs?minScore=80",
            data={"job_ids": [str(job.id) for job, _ in top]},
        )

    log.info("matching.completed", user_id=str(user.id), scored=len(jobs), shortlisted=shortlisted)
    return {"scored": len(jobs), "shortlisted": shortlisted, "rejected": rejected}


def daily_shortlist(db: Session, user_id: uuid.UUID, *, limit: int = 25) -> list[JobMatch]:
    """Ranked shortlist, newest and direct-employer roles first on ties."""
    stmt = (
        select(JobMatch)
        .join(Job, Job.id == JobMatch.job_id)
        .where(
            JobMatch.user_id == user_id,
            JobMatch.decision == MatchDecision.SHORTLISTED.value,
            JobMatch.dismissed_at.is_(None),
        )
        .order_by(
            JobMatch.score.desc(),
            Job.is_direct_employer.desc(),
            Job.posted_at.desc().nullslast(),
        )
        .limit(limit)
    )
    return list(db.execute(stmt).scalars())
