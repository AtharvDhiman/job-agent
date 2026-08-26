"""Wire shape for the per-portal readiness view (services/portal_status.py)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PortalStateOut(BaseModel):
    key: str
    display_name: str
    #: unsupported | blocked | discovery_only | authorized | ready
    status: str
    compliance_tier: str
    browser_submission_supported: bool
    automation_permitted_for_submission: bool
    #: The policy the user actually granted, or null when nothing is granted.
    granted_policy: str | None = None
    credentials_required: list[str] = Field(default_factory=list)
    credentials_present: bool
    source_count: int
    enabled_source_count: int
    last_run_at: datetime | None = None
    jobs_seen: int
    error_count: int
    #: Human-readable sentences naming exactly what is off, most severe first.
    blockers: list[str] = Field(default_factory=list)
