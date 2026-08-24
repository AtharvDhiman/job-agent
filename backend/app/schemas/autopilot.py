from __future__ import annotations

from pydantic import BaseModel, Field


class AutopilotGates(BaseModel):
    automation_enabled: bool
    global_automation_enabled: bool
    authorized_platforms: list[str]
    verified_fact_count: int
    unverified_fact_count: int
    enabled_source_count: int
    resume_uploaded: bool
    applications_today: int
    daily_application_limit: int
    queued_application_count: int


class AutopilotStatusOut(AutopilotGates):
    next_steps: list[str]


class BlockedSource(BaseModel):
    connector_key: str
    identifier: str
    error: str


class DiscoveryCounts(BaseModel):
    sources_run: int
    created: int
    updated: int
    duplicates: int
    blocked: list[BlockedSource]


class ScoringCounts(BaseModel):
    scored: int
    shortlisted: int
    rejected: int


class DraftingCounts(BaseModel):
    drafted: int
    queued_for_auto_submit: int
    sent_to_review: int


class AutopilotRunIn(BaseModel):
    include_drafting: bool = True


class AutopilotRunOut(BaseModel):
    discovery: DiscoveryCounts
    scoring: ScoringCounts
    drafting: DraftingCounts
    gates: AutopilotGates
    next_steps: list[str] = Field(default_factory=list)
