from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import case, func, or_, select

from app.api.deps import CurrentUser, DbSession, RequireOperator
from app.connectors import registry
from app.core.config import settings
from app.core.enums import AuditAction, ComplianceTier, SubmissionPolicy
from app.models.application import Application
from app.models.job import Job, JobMatch, JobSourceSubscription
from app.schemas.common import ConnectorInfo, Page
from app.schemas.jobs import (
    CompanyOut,
    DiscoveryRunOut,
    JobDetailOut,
    JobOut,
    ManualJobIn,
    MatchOut,
    MatchWithJobOut,
    SubscriptionIn,
    SubscriptionOut,
)
from app.services import audit, discovery, matching
from app.utils.text import normalize_company, normalize_title

router = APIRouter(tags=["jobs"])


@router.get("/connectors", response_model=list[ConnectorInfo])
def list_connectors() -> list[dict]:
    """Every connector with its automation policy, so nothing is implicit.

    `automation_permitted_for_submission` false means the platform is never
    automated. `requires_user_review_by_default` true means applications are
    queued for you until you explicitly authorize the platform.
    """
    return [c.describe(settings) for c in sorted(registry.all(), key=lambda x: x.key)]


# --------------------------------------------------------------- subscriptions
@router.get("/sources", response_model=list[SubscriptionOut])
def list_sources(db: DbSession, user: CurrentUser) -> list[JobSourceSubscription]:
    return list(
        db.execute(
            select(JobSourceSubscription)
            .where(JobSourceSubscription.user_id == user.id)
            .order_by(JobSourceSubscription.created_at.desc())
        ).scalars()
    )


def _check_discovery_allowed(connector_key: str) -> None:
    """Every route that points a source at a platform runs this. No exceptions.

    Creating a source and editing one are the same act with the same
    consequence -- a fetch against that platform -- so they cannot have different
    admission rules.
    """
    try:
        connector = registry.get(connector_key)
    except Exception:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown connector '{connector_key}'. Known: {registry.keys()}",
        ) from None

    if connector.compliance_tier == ComplianceTier.MANUAL_ONLY:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{connector.display_name} does not support automated discovery. "
            "Add jobs individually with POST /jobs/manual.",
        )
    available, reason = connector.is_available(settings)
    if not available:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, reason)


@router.post("/sources", response_model=SubscriptionOut, status_code=status.HTTP_201_CREATED)
def create_source(
    payload: SubscriptionIn, db: DbSession, user: RequireOperator
) -> JobSourceSubscription:
    _check_discovery_allowed(payload.connector_key)

    existing = db.execute(
        select(JobSourceSubscription).where(
            JobSourceSubscription.user_id == user.id,
            JobSourceSubscription.connector_key == payload.connector_key,
            JobSourceSubscription.identifier == payload.identifier,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That source is already configured")

    subscription = JobSourceSubscription(user_id=user.id, **payload.model_dump())
    db.add(subscription)
    db.flush()
    return subscription


@router.patch("/sources/{source_id}", response_model=SubscriptionOut)
def update_source(
    source_id: uuid.UUID, payload: SubscriptionIn, db: DbSession, user: RequireOperator
) -> JobSourceSubscription:
    subscription = db.get(JobSourceSubscription, source_id)
    if subscription is None or subscription.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source not found")

    data = payload.model_dump(exclude_unset=True)
    if "connector_key" in data and data["connector_key"] != subscription.connector_key:
        # Re-point a source and you have created a different source. It goes
        # through the same admission checks the POST route runs, or the PATCH
        # would be a way around them.
        _check_discovery_allowed(data["connector_key"])

    was_blocked = subscription.last_status == "blocked_by_policy"
    if was_blocked and data.get("enabled") and not subscription.enabled:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This source was disabled because fetching it is not permitted: "
            f"{subscription.last_error or 'blocked by policy'} "
            "That is not a transient failure, so it is not re-enabled by editing. "
            "Delete it and add the source you are actually allowed to read.",
        )

    for key, value in data.items():
        setattr(subscription, key, value)
    if subscription.enabled and not was_blocked:
        subscription.consecutive_failures = 0
    db.flush()
    audit.record(
        db,
        "job_source.updated",
        user_id=user.id,
        actor=user.email,
        object_type="job_source_subscription",
        object_id=str(subscription.id),
        payload={"changed": sorted(data)},
    )
    return subscription


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: uuid.UUID, db: DbSession, user: RequireOperator) -> None:
    subscription = db.get(JobSourceSubscription, source_id)
    if subscription is None or subscription.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source not found")
    db.delete(subscription)


@router.post("/discovery/run", response_model=DiscoveryRunOut)
def run_discovery(db: DbSession, user: RequireOperator, score: bool = True) -> DiscoveryRunOut:
    """Fetch every enabled source now, then score the new jobs."""
    results = discovery.run_all_for_user(db, user.id)
    if score:
        matching.score_for_user(db, user)
    payload = [r.as_dict() for r in results]
    return DiscoveryRunOut(
        results=payload,
        total_created=sum(r.created for r in results),
        total_updated=sum(r.updated for r in results),
        total_duplicates=sum(r.duplicates for r in results),
        blocked=[r for r in payload if r["status"] == "blocked_by_policy"],
    )


# ----------------------------------------------------------------------- jobs
@router.post("/jobs/manual", response_model=JobDetailOut, status_code=status.HTTP_201_CREATED)
def create_manual_job(payload: ManualJobIn, db: DbSession, user: RequireOperator) -> Job:
    """Record a posting yourself. Use this for any site that blocks automation."""
    now = datetime.now(UTC)
    from app.services.locations import resolve_city, resolve_country
    from app.services.normalizer import compute_dedupe_hash

    country = resolve_country(payload.location_raw)
    job = Job(
        connector_key="manual",
        compliance_tier=ComplianceTier.MANUAL_ONLY.value,
        submission_policy_default=SubmissionPolicy.REVIEW_REQUIRED.value,
        external_id=f"manual:{uuid.uuid4()}",
        source_url=payload.source_url,
        apply_url=payload.apply_url or payload.source_url,
        is_direct_employer=True,
        title=payload.title,
        title_normalized=normalize_title(payload.title),
        company=payload.company,
        company_normalized=normalize_company(payload.company),
        description_text=payload.description_text,
        location_raw=payload.location_raw,
        location_city=resolve_city(payload.location_raw),
        location_country=country,
        work_arrangement=payload.work_arrangement.value,
        employment_type=payload.employment_type.value,
        seniority=payload.seniority.value,
        salary_min=payload.salary_min,
        salary_max=payload.salary_max,
        salary_currency=payload.salary_currency,
        salary_period=payload.salary_period,
        posted_at=payload.posted_at or now,
        deadline_at=payload.deadline_at,
        first_seen_at=now,
        last_seen_at=now,
        dedupe_hash=compute_dedupe_hash(payload.company, payload.title, country),
        # `jobs` is a shared corpus -- GET /jobs/{id} serves any job to any
        # authenticated account so duplicates can be collapsed across users --
        # and JobDetailOut exposes `raw` verbatim. Stamping the creator's user id
        # in here published it to everybody. Nothing reads it back.
        raw={"source": "manual"},
    )
    db.add(job)
    db.flush()
    audit.record(
        db,
        AuditAction.JOB_INGESTED,
        user_id=user.id,
        actor=user.email,
        object_type="job",
        object_id=str(job.id),
        payload={"source": "manual"},
    )
    return job


# ------------------------------------------------------------------ companies
@router.get("/companies", response_model=Page[CompanyOut])
def list_companies(
    db: DbSession,
    user: CurrentUser,
    q: str = "",
    country: str | None = None,
    connector_key: str | None = None,
    posted_within_hours: int | None = Query(default=None, ge=1, le=2160),
    scored_only: bool = False,
    sort: str = Query(default="jobs", pattern="^(jobs|recent|score|name)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[CompanyOut]:
    """Every employer that has posted a job we have ingested, rolled up.

    Aggregation is done in three indexed queries plus a merge in Python rather
    than one query with a string aggregate, because `string_agg` (PostgreSQL) and
    `group_concat` (SQLite) are not the same function and this has to run on
    both.

    Duplicates never inflate a count: only canonical rows are considered, so a
    role found on Greenhouse and again on an aggregator is one job here.
    """
    base = select(Job).where(Job.canonical_job_id.is_(None), Job.company != "")
    if q:
        like = f"%{q.lower()}%"
        base = base.where(func.lower(Job.company).like(like))
    if country:
        base = base.where(Job.location_country == country.upper())
    if connector_key:
        base = base.where(Job.connector_key == connector_key)
    if posted_within_hours:
        cutoff = datetime.now(UTC) - timedelta(hours=posted_within_hours)
        base = base.where(or_(Job.posted_at >= cutoff, Job.first_seen_at >= cutoff))

    filtered = base.subquery()

    # 1. Per-company counts and dates.
    rollup = select(
        filtered.c.company_normalized.label("key"),
        func.min(filtered.c.company).label("company"),
        func.count(filtered.c.id).label("job_count"),
        func.sum(case((filtered.c.closed_at.is_(None), 1), else_=0)).label("open_count"),
        func.max(filtered.c.posted_at).label("latest_posted_at"),
        func.min(filtered.c.first_seen_at).label("first_seen_at"),
        func.max(case((filtered.c.is_direct_employer.is_(True), 1), else_=0)).label("direct"),
    ).group_by(filtered.c.company_normalized)

    rows = {r.key: r for r in db.execute(rollup).all()}
    if not rows:
        return Page[CompanyOut](items=[], total=0, limit=limit, offset=offset)

    # 2. Distinct facets per company, merged in Python.
    facets: dict[str, dict[str, set[str]]] = {
        key: {"connectors": set(), "countries": set(), "arrangements": set()} for key in rows
    }
    facet_rows = db.execute(
        select(
            filtered.c.company_normalized,
            filtered.c.connector_key,
            filtered.c.location_country,
            filtered.c.work_arrangement,
        ).distinct()
    ).all()
    for key, connector, country_code, arrangement in facet_rows:
        bucket = facets.get(key)
        if bucket is None:
            continue
        bucket["connectors"].add(connector)
        if country_code:
            bucket["countries"].add(country_code)
        if arrangement and arrangement != "unknown":
            bucket["arrangements"].add(arrangement)

    # 3. This user's best score per company, and how many they have applied to.
    score_rows = db.execute(
        select(
            filtered.c.company_normalized,
            func.max(JobMatch.score),
            func.count(JobMatch.id),
        )
        .join(JobMatch, JobMatch.job_id == filtered.c.id)
        .where(JobMatch.user_id == user.id)
        .group_by(filtered.c.company_normalized)
    ).all()
    scores = {key: (best, count) for key, best, count in score_rows}

    applied_rows = db.execute(
        select(filtered.c.company_normalized, func.count(Application.id))
        .join(Application, Application.job_id == filtered.c.id)
        .where(Application.user_id == user.id)
        .group_by(filtered.c.company_normalized)
    ).all()
    applied = dict(applied_rows)

    items = [
        CompanyOut(
            company=row.company,
            company_normalized=key,
            job_count=int(row.job_count),
            open_job_count=int(row.open_count or 0),
            latest_posted_at=row.latest_posted_at,
            first_seen_at=row.first_seen_at,
            connectors=sorted(facets[key]["connectors"]),
            countries=sorted(facets[key]["countries"]),
            work_arrangements=sorted(facets[key]["arrangements"]),
            direct_employer=bool(row.direct),
            best_score=scores.get(key, (None, 0))[0],
            scored_job_count=scores.get(key, (None, 0))[1],
            applied_count=applied.get(key, 0),
        )
        for key, row in rows.items()
    ]

    if scored_only:
        items = [item for item in items if item.scored_job_count > 0]

    epoch = datetime.min.replace(tzinfo=UTC)

    def most_recent(company: CompanyOut) -> float:
        stamp = company.latest_posted_at or company.first_seen_at or epoch
        return -stamp.timestamp()

    sorters = {
        "jobs": lambda c: (-c.job_count, c.company.lower()),
        "name": lambda c: c.company.lower(),
        "score": lambda c: (-(c.best_score or -1), -c.job_count),
        "recent": most_recent,
    }
    items.sort(key=sorters[sort])

    return Page[CompanyOut](
        items=items[offset : offset + limit],
        total=len(items),
        limit=limit,
        offset=offset,
    )


@router.get("/jobs", response_model=Page[MatchWithJobOut])
def list_jobs(
    db: DbSession,
    user: CurrentUser,
    q: str = "",
    min_score: int | None = Query(default=None, ge=0, le=100),
    decision: str | None = None,
    country: str | None = None,
    work_arrangement: str | None = None,
    seniority: str | None = None,
    connector_key: str | None = None,
    posted_within_hours: int | None = Query(default=None, ge=1, le=2160),
    direct_only: bool = False,
    include_duplicates: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[MatchWithJobOut]:
    stmt = (
        select(JobMatch, Job)
        .join(Job, Job.id == JobMatch.job_id)
        .where(JobMatch.user_id == user.id)
    )
    if not include_duplicates:
        stmt = stmt.where(Job.canonical_job_id.is_(None))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Job.title).like(like), func.lower(Job.company).like(like)))
    if min_score is not None:
        stmt = stmt.where(JobMatch.score >= min_score)
    if decision:
        stmt = stmt.where(JobMatch.decision == decision)
    if country:
        stmt = stmt.where(Job.location_country == country.upper())
    if work_arrangement:
        stmt = stmt.where(Job.work_arrangement == work_arrangement)
    if seniority:
        stmt = stmt.where(Job.seniority == seniority)
    if connector_key:
        stmt = stmt.where(Job.connector_key == connector_key)
    if direct_only:
        stmt = stmt.where(Job.is_direct_employer.is_(True))
    if posted_within_hours:
        cutoff = datetime.now(UTC) - timedelta(hours=posted_within_hours)
        stmt = stmt.where(or_(Job.posted_at >= cutoff, Job.first_seen_at >= cutoff))

    total = int(db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one())
    rows = db.execute(
        stmt.order_by(JobMatch.score.desc(), Job.posted_at.desc().nullslast())
        .limit(limit)
        .offset(offset)
    ).all()
    return Page[MatchWithJobOut](
        items=[
            MatchWithJobOut(match=MatchOut.model_validate(match), job=JobOut.model_validate(job))
            for match, job in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/jobs/shortlist", response_model=list[MatchWithJobOut])
def shortlist(db: DbSession, user: CurrentUser, limit: int = Query(default=25, ge=1, le=100)):
    matches = matching.daily_shortlist(db, user.id, limit=limit)
    return [
        MatchWithJobOut(
            match=MatchOut.model_validate(m), job=JobOut.model_validate(db.get(Job, m.job_id))
        )
        for m in matches
    ]


@router.get("/jobs/{job_id}", response_model=JobDetailOut)
def get_job(job_id: uuid.UUID, db: DbSession, user: CurrentUser) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job


@router.get("/jobs/{job_id}/duplicates", response_model=list[JobOut])
def job_duplicates(job_id: uuid.UUID, db: DbSession, user: CurrentUser) -> list[Job]:
    """Same role found on other boards, collapsed under this canonical record."""
    return list(db.execute(select(Job).where(Job.canonical_job_id == job_id)).scalars())


@router.post("/matches/{match_id}/dismiss", response_model=MatchOut)
def dismiss_match(match_id: uuid.UUID, db: DbSession, user: RequireOperator) -> JobMatch:
    match = db.get(JobMatch, match_id)
    if match is None or match.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Match not found")
    match.dismissed_at = datetime.now(UTC)
    db.flush()
    return match


@router.post("/matches/rescore", response_model=dict)
def rescore(db: DbSession, user: RequireOperator, limit: int = Query(default=500, ge=1, le=2000)):
    """Re-run scoring after a profile change. Existing matches are cleared first."""
    db.execute(JobMatch.__table__.delete().where(JobMatch.user_id == user.id))
    db.flush()
    return matching.score_for_user(db, user, limit=limit)
