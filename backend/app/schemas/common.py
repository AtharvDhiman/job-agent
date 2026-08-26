from __future__ import annotations

import uuid
from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Message(BaseModel):
    detail: str


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


class IdResponse(BaseModel):
    id: uuid.UUID


class TimestampedOut(ORMModel):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ConnectorInfo(BaseModel):
    key: str
    display_name: str
    compliance_tier: str
    submission_policy_default: str
    automation_permitted_for_discovery: bool
    browser_submission_supported: bool
    automation_permitted_for_submission: bool
    requires_user_review_by_default: bool
    policy_note: str
    required_credentials: list[str]
    available: bool
    unavailable_reason: str
    direct_employer: bool
    identifier_label: str
    identifier_help: str


class HealthOut(BaseModel):
    status: str
    version: str
    environment: str
    database: str
    redis: str
    llm: str
    automation_enabled: bool
    checks: dict = Field(default_factory=dict)
