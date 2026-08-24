"""Job ingestion: fetch -> normalise -> deduplicate -> upsert.

Every run is recorded on the subscription (status, error, failure streak) and in
the audit log. A BlockedByPolicyError is terminal for that source: we mark it and
stop, rather than retrying something we are not allowed to do.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors import BlockedByPolicyError, ConnectorError, PoliteClient, SourceSpec, registry
from app.core.config import settings
from app.core.enums import AuditAction
from app.core.logging import get_logger
from app.models.job import Job, JobSourceSubscription
from app.services import audit, normalizer

log = get_logger(__name__)

MAX_CONSECUTIVE_FAILURES = 5


@dataclass(slots=True)
class DiscoveryStats:
    subscription_id: str = ""
    connector_key: str = ""
    identifier: str = ""
    fetched: int = 0
    created: int = 0
    updated: int = 0
    duplicates: int = 0
    status: str = "ok"
    error: str = ""
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "subscription_id": self.subscription_id,
            "connector_key": self.connector_key,
            "identifier": self.identifier,
            "fetched": self.fetched,
            "created": self.created,
            "updated": self.updated,
            "duplicates": self.duplicates,
            "status": self.status,
            "error": self.error,
            "notes": self.notes,
        }


def _find_canonical(db: Session, job: Job) -> Job | None:
    """An existing job with the same dedupe hash becomes the canonical record."""
    if not job.dedupe_hash:
        return None
    stmt = (
        select(Job)
        .where(Job.dedupe_hash == job.dedupe_hash, Job.canonical_job_id.is_(None))
        .order_by(Job.first_seen_at.asc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


_REFRESHABLE = (
    "title",
    "title_normalized",
    "company",
    "company_normalized",
    "department",
    "description_text",
    "description_html",
    "location_raw",
    "location_city",
    "location_country",
    "work_arrangement",
    "employment_type",
    "seniority",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
    "posted_at",
    "deadline_at",
    "apply_url",
    "source_url",
    "extracted_skills",
    "requirements",
    "visa_sponsorship_mentioned",
    "dedupe_hash",
)


def upsert_job(db: Session, candidate: Job) -> tuple[Job, str]:
    """Insert, refresh, or link as a duplicate.

    Returns (job, outcome) where outcome is 'created', 'updated' or 'duplicate'.
    """
    existing = db.execute(
        select(Job).where(
            Job.connector_key == candidate.connector_key,
            Job.external_id == candidate.external_id,
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.last_seen_at = candidate.last_seen_at
        for attribute in _REFRESHABLE:
            setattr(existing, attribute, getattr(candidate, attribute))
        return existing, "updated"

    canonical = _find_canonical(db, candidate)
    if canonical is not None:
        if candidate.is_direct_employer and not canonical.is_direct_employer:
            # Prefer the employer's own listing as the canonical record.
            db.add(candidate)
            db.flush()
            canonical.canonical_job_id = candidate.id
            return candidate, "created"
        candidate.canonical_job_id = canonical.id
        db.add(candidate)
        db.flush()
        return candidate, "duplicate"

    db.add(candidate)
    db.flush()
    return candidate, "created"


def run_subscription(
    db: Session, subscription: JobSourceSubscription, *, client: PoliteClient | None = None
) -> DiscoveryStats:
    stats = DiscoveryStats(
        subscription_id=str(subscription.id),
        connector_key=subscription.connector_key,
        identifier=subscription.identifier,
    )
    owns_client = client is None
    client = client or PoliteClient()
    try:
        connector_cls = registry.get(subscription.connector_key)
        available, reason = connector_cls.is_available(settings)
        if not available:
            raise BlockedByPolicyError(reason)

        connector = connector_cls(client, settings=settings)
        spec = SourceSpec(
            connector_key=subscription.connector_key,
            identifier=subscription.identifier,
            display_name=subscription.display_name,
            config=subscription.config or {},
        )
        result = connector.fetch(spec, etag=subscription.etag or "")
        stats.fetched = len(result.jobs)
        stats.notes = list(result.notes)
        now = datetime.now(UTC)

        for raw in result.jobs:
            candidate = normalizer.normalize(raw, connector_cls, now=now)
            _job, outcome = upsert_job(db, candidate)
            if outcome == "created":
                stats.created += 1
            elif outcome == "updated":
                stats.updated += 1
            else:
                stats.duplicates += 1

        subscription.etag = result.etag or subscription.etag
        subscription.last_status = "ok"
        subscription.last_error = ""
        subscription.consecutive_failures = 0
        subscription.jobs_seen += stats.fetched

    except BlockedByPolicyError as exc:
        stats.status, stats.error = "blocked_by_policy", str(exc)
        subscription.last_status = "blocked_by_policy"
        subscription.last_error = str(exc)[:1000]
        subscription.enabled = False  # never retry something we are not allowed to do
        audit.record(
            db,
            AuditAction.POLICY_BLOCK,
            user_id=subscription.user_id,
            object_type="job_source_subscription",
            object_id=str(subscription.id),
            outcome="blocked",
            payload={"connector": subscription.connector_key, "reason": str(exc)[:500]},
        )
        log.warning("discovery.blocked", connector=subscription.connector_key, reason=str(exc))

    except ConnectorError as exc:
        stats.status, stats.error = "error", str(exc)
        subscription.last_status = "error"
        subscription.last_error = str(exc)[:1000]
        subscription.consecutive_failures += 1
        if subscription.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            subscription.enabled = False
            stats.notes.append(f"Disabled after {MAX_CONSECUTIVE_FAILURES} consecutive failures.")
        log.warning("discovery.error", connector=subscription.connector_key, error=str(exc))

    finally:
        subscription.last_run_at = datetime.now(UTC)
        if owns_client:
            client.close()

    audit.record(
        db,
        AuditAction.DISCOVERY_RUN,
        user_id=subscription.user_id,
        object_type="job_source_subscription",
        object_id=str(subscription.id),
        outcome=stats.status,
        payload=stats.as_dict(),
    )
    return stats


def run_all_for_user(db: Session, user_id: uuid.UUID) -> list[DiscoveryStats]:
    subscriptions = list(
        db.execute(
            select(JobSourceSubscription).where(
                JobSourceSubscription.user_id == user_id,
                JobSourceSubscription.enabled.is_(True),
            )
        ).scalars()
    )
    results: list[DiscoveryStats] = []
    with PoliteClient() as client:
        for subscription in subscriptions:
            results.append(run_subscription(db, subscription, client=client))
            db.flush()
    return results
