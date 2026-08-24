"""Celery application and beat schedule."""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings
from app.core.logging import configure_logging

configure_logging(settings.log_level, settings.log_format)

celery_app = Celery(
    "jobagent",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone=settings.notify_timezone,
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=900,
    task_soft_time_limit=840,
    result_expires=86400,
    broker_connection_retry_on_startup=True,
    task_default_retry_delay=60,
    task_annotations={
        "app.workers.tasks.run_discovery_for_user": {"rate_limit": "6/m"},
    },
)

celery_app.conf.beat_schedule = {
    "discover-and-score": {
        "task": "app.workers.tasks.discover_all_users",
        "schedule": settings.discovery_interval_minutes * 60,
        "options": {"expires": settings.discovery_interval_minutes * 60},
    },
    "draft-shortlisted": {
        "task": "app.workers.tasks.draft_shortlisted_applications",
        "schedule": 30 * 60,
    },
    "daily-digest": {
        "task": "app.workers.tasks.send_daily_digests",
        "schedule": crontab(hour=settings.notify_digest_hour_local, minute=0),
    },
    "expire-stale-reviews": {
        "task": "app.workers.tasks.expire_stale_reviews",
        "schedule": crontab(hour=3, minute=30),
    },
    "prune-expired-tokens": {
        "task": "app.workers.tasks.prune_expired_tokens",
        "schedule": crontab(hour=4, minute=0),
    },
}
