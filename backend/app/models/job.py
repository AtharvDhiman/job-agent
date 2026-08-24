from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import (
    ComplianceTier,
    EmploymentType,
    MatchDecision,
    Seniority,
    SubmissionPolicy,
    WorkArrangement,
)
from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.db.types import GUID, JSONType


class JobSourceSubscription(UUIDPrimaryKey, Timestamps, Base):
    """One configured board to poll, e.g. Greenhouse board token 'stripe'."""

    __tablename__ = "job_source_subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", "connector_key", "identifier", name="uq_source_sub"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    connector_key: Mapped[str] = mapped_column(String(64), nullable=False)
    identifier: Mapped[str] = mapped_column(String(300), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config: Mapped[dict] = mapped_column(JSONType, default=dict)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str] = mapped_column(String(32), default="never_run")
    last_error: Mapped[str] = mapped_column(String(1000), default="")
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    etag: Mapped[str] = mapped_column(String(300), default="")
    jobs_seen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Job(UUIDPrimaryKey, Timestamps, Base):
    """A normalised posting. `dedupe_hash` collapses the same role across boards."""

    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("connector_key", "external_id", name="uq_job_external"),
        Index("ix_jobs_dedupe", "dedupe_hash"),
        Index("ix_jobs_posted_at", "posted_at"),
        Index("ix_jobs_company_title", "company_normalized", "title_normalized"),
    )

    connector_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    compliance_tier: Mapped[str] = mapped_column(
        String(32), default=ComplianceTier.PUBLIC_JOB_API.value, nullable=False
    )
    submission_policy_default: Mapped[str] = mapped_column(
        String(32), default=SubmissionPolicy.REVIEW_REQUIRED.value, nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(300), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1000), default="")
    apply_url: Mapped[str] = mapped_column(String(1000), default="")
    is_direct_employer: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    title: Mapped[str] = mapped_column(String(400), nullable=False)
    title_normalized: Mapped[str] = mapped_column(String(400), default="", index=True)
    company: Mapped[str] = mapped_column(String(300), default="")
    company_normalized: Mapped[str] = mapped_column(String(300), default="", index=True)
    department: Mapped[str] = mapped_column(String(200), default="")

    description_text: Mapped[str] = mapped_column(Text, default="")
    description_html: Mapped[str] = mapped_column(Text, default="")

    location_raw: Mapped[str] = mapped_column(String(400), default="")
    location_city: Mapped[str] = mapped_column(String(160), default="")
    location_country: Mapped[str] = mapped_column(String(2), default="", index=True)
    work_arrangement: Mapped[str] = mapped_column(
        String(16), default=WorkArrangement.UNKNOWN.value, index=True
    )
    employment_type: Mapped[str] = mapped_column(String(20), default=EmploymentType.UNKNOWN.value)
    seniority: Mapped[str] = mapped_column(String(20), default=Seniority.UNKNOWN.value, index=True)

    salary_min: Mapped[int | None] = mapped_column(Integer)
    salary_max: Mapped[int | None] = mapped_column(Integer)
    salary_currency: Mapped[str] = mapped_column(String(3), default="")
    salary_period: Mapped[str] = mapped_column(String(16), default="")

    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    extracted_skills: Mapped[list] = mapped_column(JSONType, default=list)
    requirements: Mapped[list] = mapped_column(JSONType, default=list)
    visa_sponsorship_mentioned: Mapped[bool | None] = mapped_column(Boolean)
    raw: Mapped[dict] = mapped_column(JSONType, default=dict)

    dedupe_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    canonical_job_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("jobs.id", ondelete="SET NULL")
    )

    matches = relationship("JobMatch", back_populates="job", cascade="all, delete-orphan")

    @property
    def is_duplicate(self) -> bool:
        return self.canonical_job_id is not None


class JobMatch(UUIDPrimaryKey, Timestamps, Base):
    """Score plus a full, human-readable explanation. One row per (user, job)."""

    __tablename__ = "job_matches"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_match_user_job"),
        Index("ix_matches_score", "user_id", "score"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    decision: Mapped[str] = mapped_column(
        String(32), default=MatchDecision.BELOW_THRESHOLD.value, index=True
    )
    component_scores: Mapped[dict] = mapped_column(JSONType, default=dict)
    matching_skills: Mapped[list] = mapped_column(JSONType, default=list)
    missing_skills: Mapped[list] = mapped_column(JSONType, default=list)
    risks: Mapped[list] = mapped_column(JSONType, default=list)
    hard_filter_failures: Mapped[list] = mapped_column(JSONType, default=list)
    explanation: Mapped[str] = mapped_column(Text, default="")
    semantic_similarity: Mapped[float] = mapped_column(Float, default=0.0)
    scored_by: Mapped[str] = mapped_column(String(32), default="deterministic")
    recommended_resume_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("documents.id", ondelete="SET NULL")
    )
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job = relationship("Job", back_populates="matches")
