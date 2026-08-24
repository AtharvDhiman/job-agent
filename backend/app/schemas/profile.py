from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from app.core.enums import DocumentKind, FactCategory, Seniority, WorkArrangement
from app.schemas.common import ORMModel


class ProfileIn(BaseModel):
    full_name: str = Field(default="", max_length=200)
    headline: str = Field(default="", max_length=300)
    contact_email: str = Field(default="", max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    location_city: str = Field(default="", max_length=120)
    location_region: str = Field(default="", max_length=120)
    location_country: str = Field(default="", max_length=2)
    timezone: str = Field(default="UTC", max_length=64)
    address: str | None = None
    linkedin_url: str = Field(default="", max_length=500)
    portfolio_urls: list[str] = Field(default_factory=list)
    target_titles: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    preferred_countries: list[str] = Field(default_factory=list)
    preferred_timezones: list[str] = Field(default_factory=list)
    work_arrangement_preference: list[WorkArrangement] = Field(default_factory=list)
    industries_priority: list[str] = Field(default_factory=list)
    companies_to_avoid: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)
    employment_types: list[str] = Field(default_factory=list)
    seniority_level: Seniority = Seniority.UNKNOWN
    years_experience: float | None = Field(default=None, ge=0, le=70)
    min_salary_amount: int | None = Field(default=None, ge=0)
    min_salary_currency: str = Field(default="USD", max_length=3)
    salary_period: str = Field(default="year", max_length=16)
    willing_to_relocate: bool | None = None
    requires_sponsorship: bool | None = None
    work_authorization: dict | None = None
    notice_period_days: int | None = Field(default=None, ge=0, le=365)
    earliest_start_date: date | None = None

    @field_validator("portfolio_urls", "linkedin_url")
    @classmethod
    def _https_only(cls, value):
        items = value if isinstance(value, list) else ([value] if value else [])
        for item in items:
            if item and not item.startswith(("http://", "https://")):
                raise ValueError(f"'{item}' must be an absolute http(s) URL")
        return value


class ProfileOut(ProfileIn, ORMModel):
    id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class CareerFactIn(BaseModel):
    category: FactCategory
    key: str = Field(default="", max_length=200)
    value: str = ""
    organization: str = Field(default="", max_length=200)
    title: str = Field(default="", max_length=200)
    location: str = Field(default="", max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False
    highlights: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    evidence_url: str = Field(default="", max_length=500)
    sensitive: bool = False


class CareerFactOut(CareerFactIn, ORMModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    verified: bool
    verified_at: datetime | None
    source_document_id: uuid.UUID | None
    created_at: datetime


class VerifyFactsIn(BaseModel):
    fact_ids: list[uuid.UUID]
    verified: bool = True


class DocumentOut(ORMModel):
    id: uuid.UUID
    kind: DocumentKind
    label: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    version: int
    is_primary: bool
    generated_for_job_id: uuid.UUID | None
    generation_meta: dict
    created_at: datetime


class UploadResult(BaseModel):
    document: DocumentOut
    proposed_fact_count: int
    warnings: list[str]
    parsed: dict
    auto_configured: dict = Field(default_factory=dict)
