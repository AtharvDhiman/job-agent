"""Background tasks.

Everything a worker does passes through the same services the API uses, so the
policy gate cannot be sidestepped by running work in the background.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from celery import shared_task
from sqlalchemy import select

from app.core.config import settings
from app.core.enums import MatchDecision, ReviewStatus
from app.core.logging import get_logger
from app.db.session import session_scope
from app.models.application import Application, ReviewTask
from app.models.job import Job, JobMatch
from app.models.user import AgentSettings, RefreshToken, User
from app.services import application_workflow as workflow
from app.services import autopilot, discovery, matching, notifications

log = get_logger("worker")


@shared_task(name="app.workers.tasks.run_discovery_for_user", bind=True, max_retries=3)
def run_discovery_for_user(self, user_id: str) -> dict:
    try:
        with session_scope() as db:
            user = db.get(User, uuid.UUID(user_id))
            if user is None or not user.is_active:
                return {"skipped": "inactive user"}
            results = discovery.run_all_for_user(db, user.id)
            scored = matching.score_for_user(db, user)
            return {
                "sources": len(results),
                "created": sum(r.created for r in results),
                "duplicates": sum(r.duplicates for r in results),
                "blocked": [r.connector_key for r in results if r.status == "blocked_by_policy"],
                **scored,
            }
    except Exception as exc:  # noqa: BLE001 - retried with exponential backoff
        log.exception("worker.discovery_failed", user_id=user_id)
        raise self.retry(exc=exc, countdown=min(600, 60 * (2**self.request.retries))) from exc


@shared_task(name="app.workers.tasks.discover_all_users")
def discover_all_users() -> dict:
    with session_scope() as db:
        user_ids = [
            str(u.id) for u in db.execute(select(User).where(User.is_active.is_(True))).scalars()
        ]
    for user_id in user_ids:
        run_discovery_for_user.delay(user_id)
    return {"queued": len(user_ids)}


@shared_task(name="app.workers.tasks.draft_shortlisted_applications")
def draft_shortlisted_applications(limit_per_user: int = 10) -> dict:
    """Draft applications for high scorers.

    Drafting is always safe: it produces documents and, unless every gate in
    policy.decide() passes, a review task. It never submits.
    """
    drafted = queued = review = 0
    with session_scope() as db:
        for user in db.execute(select(User).where(User.is_active.is_(True))).scalars():
            agent_settings = db.execute(
                select(AgentSettings).where(AgentSettings.user_id == user.id)
            ).scalar_one_or_none()
            if agent_settings is None:
                continue
            existing = select(Application.job_id).where(Application.user_id == user.id)
            matches = list(
                db.execute(
                    select(JobMatch)
                    .where(
                        JobMatch.user_id == user.id,
                        JobMatch.decision == MatchDecision.SHORTLISTED.value,
                        JobMatch.dismissed_at.is_(None),
                        JobMatch.job_id.not_in(existing),
                    )
                    .order_by(JobMatch.score.desc())
                    .limit(limit_per_user)
                ).scalars()
            )
            for match in matches:
                job = db.get(Job, match.job_id)
                if job is None:
                    continue
                result = workflow.draft_application(db, user, job, match)
                drafted += 1
                if result.decision.may_submit:
                    queued += 1
                else:
                    review += 1
                db.flush()
    log.info("worker.drafted", drafted=drafted, queued=queued, review=review)
    return {"drafted": drafted, "queued_for_auto_submit": queued, "sent_to_review": review}


@shared_task(name="app.workers.tasks.run_autopilot_for_user")
def run_autopilot_for_user(user_id: str) -> dict:
    """On-demand full pipeline for one user; not scheduled in beat.

    Enqueued when a user wants a run now (e.g. right after a resume upload)
    without waiting for the next discovery interval. Same services, same policy
    gate as everything else.
    """
    with session_scope() as db:
        user = db.get(User, uuid.UUID(user_id))
        if user is None or not user.is_active:
            return {"skipped": "inactive user"}
        result = autopilot.run_pipeline(db, user)
        return {
            "discovery": result["discovery"],
            "scoring": result["scoring"],
            "drafting": result["drafting"],
        }


@shared_task(name="app.workers.tasks.send_daily_digests")
def send_daily_digests() -> dict:
    sent = 0
    with session_scope() as db:
        for user in db.execute(select(User).where(User.is_active.is_(True))).scalars():
            notifications.send_digest(db, user.id)
            sent += 1
    return {"digests": sent}


@shared_task(name="app.workers.tasks.expire_stale_reviews")
def expire_stale_reviews(days: int = 30) -> dict:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    expired = 0
    with session_scope() as db:
        for task in db.execute(
            select(ReviewTask).where(
                ReviewTask.status == ReviewStatus.OPEN.value, ReviewTask.created_at < cutoff
            )
        ).scalars():
            task.status = ReviewStatus.EXPIRED.value
            task.resolved_at = datetime.now(UTC)
            task.resolution_note = f"Automatically expired after {days} days with no decision."
            expired += 1
    return {"expired": expired}


@shared_task(name="app.workers.tasks.prune_expired_tokens")
def prune_expired_tokens() -> dict:
    now = datetime.now(UTC)
    with session_scope() as db:
        result = db.execute(RefreshToken.__table__.delete().where(RefreshToken.expires_at < now))
        return {"deleted": result.rowcount or 0}


@shared_task(name="app.workers.tasks.health")
def health() -> dict:
    return {
        "ok": True,
        "environment": settings.app_env,
        "automation_global_enabled": settings.automation_global_enabled,
        "at": datetime.now(UTC).isoformat(),
    }
