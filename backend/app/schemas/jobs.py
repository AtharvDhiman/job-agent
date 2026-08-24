from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.core.enums import (
    ComplianceTier,
    EmploymentType,
    MatchDecision,
    Seniority,
    SubmissionPolicy,
    WorkArrangement,
)
from app.schemas.common import ORMModel


class SubscriptionIn(BaseModel):
    connector_key: str = Field(max_length=64)
    identifier: str = Field(max_length=300)
    display_name: str = Field(default="", max_length=200)
    config: dict = Field(default_factory=dict)
    enabled: bool = True


class SubscriptionOut(ORMModel):
    id: uuid.UUID
    connector_key: str
    identifier: str
    display_name: str
    enabled: bool
    config: dict
    last_run_at: datetime | None
    last_status: str
    last_error: str
    consecutive_failures: int
    jobs_seen: int
    created_at: datetime


class QuickAddIn(BaseModel):
    """Add a job you found anywhere (LinkedIn, Indeed, any site) by pasting it.

    You paste the link, the company, the title and the job description text you
    are already looking at. The agent extracts skills, salary, seniority, work
    arrangement and sponsorship from that text itself, then scores and drafts.

    Nothing is fetched from the URL: you supply the text, so this works for sites
    that forbid automated access without ever touching them programmatically.
    """

    url: str = Field(max_length=1000)
    company: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=400)
    description_text: str = Field(min_length=1)
    location_raw: str = Field(default="", max_length=400)
    draft: bool = True


class QuickAddOut(BaseModel):
    job_id: uuid.UUID
    title: str
    company: str
    score: int | None
    decision: str | None
    matching_skills: list[str] = Field(default_factory=list)
    application_id: uuid.UUID | None = None
    application_status: str | None = None
    review_task_id: uuid.UUID | None = None
    message: str


class ManualJobIn(BaseModel):
    """Add a posting from a site that blocks automation. You supply the facts."""

    title: str = Field(max_length=400)
    company: str = Field(max_length=300)
    source_url: str = Field(max_length=1000)
    apply_url: str = Field(default="", max_length=1000)
    description_text: str = ""
    location_raw: str = Field(default="", max_length=400)
    work_arrangement: WorkArrangement = WorkArrangement.UNKNOWN
    employment_type: EmploymentType = EmploymentType.UNKNOWN
    seniority: Seniority = Seniority.UNKNOWN
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str = Field(default="", max_length=3)
    salary_period: str = Field(default="", max_length=16)
    posted_at: datetime | None = None
    deadline_at: datetime | None = None


class JobOut(ORMModel):
    id: uuid.UUID
    connector_key: str
    compliance_tier: ComplianceTier
    submission_policy_default: SubmissionPolicy
    external_id: str
    source_url: str
    apply_url: str
    is_direct_employer: bool
    title: str
    company: str
    department: str
    location_raw: str
    location_city: str
    location_country: str
    work_arrangement: WorkArrangement
    employment_type: EmploymentType
    seniority: Seniority
    salary_min: int | None
    salary_max: int | None
    salary_currency: str
    salary_period: str
    posted_at: datetime | None
    deadline_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    extracted_skills: list[str]
    visa_sponsorship_mentioned: bool | None
    canonical_job_id: uuid.UUID | None


class JobDetailOut(JobOut):
    description_text: str
    requirements: list[str]
    raw: dict


class MatchOut(ORMModel):
    id: uuid.UUID
    job_id: uuid.UUID
    score: int
    decision: MatchDecision
    component_scores: dict
    matching_skills: list[str]
    missing_skills: list[str]
    risks: list[str]
    hard_filter_failures: list[str]
    explanation: str
    semantic_similarity: float
    scored_by: str
    dismissed_at: datetime | None
    created_at: datetime


class MatchWithJobOut(BaseModel):
    match: MatchOut
    job: JobOut


class CompanyOut(BaseModel):
    """One employer, with everything they have posted rolled up.

    Duplicates are excluded: a role found on three boards counts once, under the
    canonical listing.
    """

    company: str
    company_normalized: str
    job_count: int
    open_job_count: int
    latest_posted_at: datetime | None
    first_seen_at: datetime | None
    connectors: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    work_arrangements: list[str] = Field(default_factory=list)
    direct_employer: bool
    best_score: int | None = None
    scored_job_count: int = 0
    applied_count: int = 0


class JobFilter(BaseModel):
    q: str = ""
    min_score: int | None = None
    max_score: int | None = None
    decision: MatchDecision | None = None
    countries: list[str] = Field(default_factory=list)
    work_arrangement: WorkArrangement | None = None
    seniority: Seniority | None = None
    connector_key: str = ""
    posted_within_hours: int | None = None
    direct_only: bool = False
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class DiscoveryRunOut(BaseModel):
    results: list[dict]
    total_created: int
    total_updated: int
    total_duplicates: int
    blocked: list[dict] = Field(default_factory=list)
