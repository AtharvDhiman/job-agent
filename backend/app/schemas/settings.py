from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.enums import SubmissionPolicy
from app.schemas.common import ORMModel

#: The exact phrase a user must type to authorize automated submission.
AUTHORIZATION_ACKNOWLEDGEMENT = (
    "I have read and accept this platform's terms and authorize automated submission"
)


class AgentSettingsIn(BaseModel):
    automation_enabled: bool | None = None
    paused_reason: str = Field(default="", max_length=300)
    auto_submit_min_score: int | None = Field(default=None, ge=0, le=100)
    daily_application_limit: int | None = Field(default=None, ge=0, le=200)
    job_max_age_hours: int | None = Field(default=None, ge=1, le=720)
    discovery_interval_minutes: int | None = Field(default=None, ge=15, le=1440)
    shortlist_min_score: int | None = Field(default=None, ge=0, le=100)
    notify_channels: dict | None = None
    digest_hour_local: int | None = Field(default=None, ge=0, le=23)
    timezone: str | None = Field(default=None, max_length=64)


class AgentSettingsOut(ORMModel):
    id: uuid.UUID
    automation_enabled: bool
    paused_reason: str
    auto_submit_min_score: int
    daily_application_limit: int
    job_max_age_hours: int
    discovery_interval_minutes: int
    shortlist_min_score: int
    notify_channels: dict
    digest_hour_local: int
    timezone: str
    updated_at: datetime


class AuthorizationIn(BaseModel):
    """Granting automation requires typing the acknowledgement verbatim."""

    platform_key: str = Field(max_length=64)
    policy: SubmissionPolicy
    acknowledgement: str
    notes: str = Field(default="", max_length=500)

    @field_validator("acknowledgement")
    @classmethod
    def _must_match(cls, value: str) -> str:
        if value.strip() != AUTHORIZATION_ACKNOWLEDGEMENT:
            raise ValueError(
                "Acknowledgement text does not match. To authorize automation you must type "
                f'exactly: "{AUTHORIZATION_ACKNOWLEDGEMENT}"'
            )
        return value.strip()

    @field_validator("policy")
    @classmethod
    def _only_automation_policies(cls, value: SubmissionPolicy) -> SubmissionPolicy:
        if value not in (SubmissionPolicy.ASSISTED_AUTOFILL, SubmissionPolicy.AUTO_SUBMIT):
            raise ValueError(
                "Use this endpoint only to grant assisted_autofill or auto_submit. "
                "Revoke to return a platform to review_required."
            )
        return value


class AuthorizationOut(ORMModel):
    id: uuid.UUID
    platform_key: str
    policy: SubmissionPolicy
    granted_at: datetime | None
    revoked_at: datetime | None
    notes: str
    is_active: bool


class PauseIn(BaseModel):
    reason: str = Field(default="Paused from the dashboard", max_length=300)


class DashboardOut(BaseModel):
    automation_enabled: bool
    global_automation_enabled: bool
    paused_reason: str
    applications_today: int
    daily_application_limit: int
    auto_submit_min_score: int
    new_matches: int
    shortlisted: int
    awaiting_review: int
    auto_submitted: int
    rejected_or_skipped: int
    pipeline: dict[str, int]
    unread_notifications: int
    llm_mode: str
    top_matches: list[dict] = Field(default_factory=list)
    recent_activity: list[dict] = Field(default_factory=list)
    rejection_reasons: list[dict] = Field(default_factory=list)
    #: The six "what is the agent doing" counters, each {count, link}. The
    #: failed_or_stopped bucket also carries failure_reasons so the UI can name
    #: the exact ReviewReason instead of showing an unexplained number.
    buckets: dict[str, dict] = Field(default_factory=dict)
    #: Why the numbers above are all zero, when they are. Without this the UI
    #: cannot tell "nothing matched yet" from "you never added a source".
    empty_state: dict = Field(default_factory=dict)


class ExportOut(BaseModel):
    generated_at: datetime
    user: dict
    profile: dict | None
    career_facts: list[dict]
    documents: list[dict]
    jobs: list[dict]
    matches: list[dict]
    applications: list[dict]
    review_tasks: list[dict]
    notifications: list[dict]
    audit_log: list[dict]


class EraseIn(BaseModel):
    confirmation: str

    @field_validator("confirmation")
    @classmethod
    def _confirm(cls, value: str) -> str:
        if value.strip() != "DELETE MY DATA":
            raise ValueError('Type exactly "DELETE MY DATA" to confirm irreversible erasure')
        return value
