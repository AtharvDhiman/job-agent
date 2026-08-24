from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import DocumentKind, FactCategory
from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.db.types import GUID, EncryptedJSON, EncryptedString, JSONType


class CandidateProfile(UUIDPrimaryKey, Timestamps, Base):
    """Identity and search preferences. Sensitive fields are encrypted at rest."""

    __tablename__ = "candidate_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(200), default="")
    headline: Mapped[str] = mapped_column(String(300), default="")
    contact_email: Mapped[str] = mapped_column(String(320), default="")
    phone: Mapped[str | None] = mapped_column(EncryptedString)
    location_city: Mapped[str] = mapped_column(String(120), default="")
    location_region: Mapped[str] = mapped_column(String(120), default="")
    location_country: Mapped[str] = mapped_column(String(2), default="")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    address: Mapped[str | None] = mapped_column(EncryptedString)

    linkedin_url: Mapped[str] = mapped_column(String(500), default="")
    portfolio_urls: Mapped[list] = mapped_column(JSONType, default=list)

    target_titles: Mapped[list] = mapped_column(JSONType, default=list)
    skills: Mapped[list] = mapped_column(JSONType, default=list)
    preferred_countries: Mapped[list] = mapped_column(JSONType, default=list)
    preferred_timezones: Mapped[list] = mapped_column(JSONType, default=list)
    work_arrangement_preference: Mapped[list] = mapped_column(JSONType, default=list)
    industries_priority: Mapped[list] = mapped_column(JSONType, default=list)
    companies_to_avoid: Mapped[list] = mapped_column(JSONType, default=list)
    excluded_keywords: Mapped[list] = mapped_column(JSONType, default=list)
    employment_types: Mapped[list] = mapped_column(JSONType, default=list)

    seniority_level: Mapped[str] = mapped_column(String(32), default="unknown")
    years_experience: Mapped[float | None] = mapped_column(Numeric(4, 1))
    min_salary_amount: Mapped[int | None] = mapped_column(Integer)
    min_salary_currency: Mapped[str] = mapped_column(String(3), default="USD")
    salary_period: Mapped[str] = mapped_column(String(16), default="year")
    #: Tri-state on purpose. NULL means "you have not told us", which is never
    #: the same as "no" -- see services/answers.py.
    willing_to_relocate: Mapped[bool | None] = mapped_column(Boolean)
    requires_sponsorship: Mapped[bool | None] = mapped_column(Boolean)
    work_authorization: Mapped[dict | None] = mapped_column(EncryptedJSON)
    notice_period_days: Mapped[int | None] = mapped_column(Integer)
    earliest_start_date: Mapped[date | None] = mapped_column(Date)

    user = relationship("User", back_populates="profile")
    facts = relationship("CareerFact", back_populates="profile", cascade="all, delete-orphan")


class CareerFact(UUIDPrimaryKey, Timestamps, Base):
    """The ONLY permitted source for application content. Nothing else is used.

    `verified` is set by the human. Unverified facts can never reach a document
    or an answer -- see services/fact_guard.py.
    """

    __tablename__ = "career_facts"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("candidate_profiles.id", ondelete="CASCADE"), index=True, nullable=False
    )
    category: Mapped[str] = mapped_column(String(32), default=FactCategory.SKILL.value)
    key: Mapped[str] = mapped_column(String(200), default="")
    value: Mapped[str] = mapped_column(Text, default="")
    organization: Mapped[str] = mapped_column(String(200), default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    highlights: Mapped[list] = mapped_column(JSONType, default=list)
    tags: Mapped[list] = mapped_column(JSONType, default=list)
    evidence_url: Mapped[str] = mapped_column(String(500), default="")
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("documents.id", ondelete="SET NULL")
    )
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    profile = relationship("CandidateProfile", back_populates="facts")


class Document(UUIDPrimaryKey, Timestamps, Base):
    """Uploaded or generated file. Content-addressed, versioned, never overwritten."""

    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("user_id", "sha256", "kind", name="uq_document_content"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), default=DocumentKind.OTHER.value, index=True)
    label: Mapped[str] = mapped_column(String(200), default="")
    filename: Mapped[str] = mapped_column(String(300), default="")
    content_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    storage_key: Mapped[str] = mapped_column(String(500), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True, default="")
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("documents.id", ondelete="SET NULL")
    )
    #: The raw text of an uploaded resume: name, phone, address, employment
    #: history -- the densest block of personal data the product holds. It was
    #: the one large PII column left in the clear while phone and address were
    #: encrypted two tables over.
    extracted_text: Mapped[str | None] = mapped_column(EncryptedString, default="")
    #: Parsed contact details (emails, phones, links) extracted from that resume.
    parsed: Mapped[dict | None] = mapped_column(EncryptedJSON, default=dict)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    generated_for_job_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("jobs.id", ondelete="SET NULL")
    )
    generation_meta: Mapped[dict] = mapped_column(JSONType, default=dict)
