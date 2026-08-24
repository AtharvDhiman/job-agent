from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.core.enums import (
    ApplicationStatus,
    NotificationKind,
    PipelineStage,
    QuestionType,
    ReviewReason,
    ReviewStatus,
    SubmissionPolicy,
)
from app.core.enums import (
    PipelineStage as Stage,
)
from app.schemas.common import ORMModel
from app.schemas.jobs import JobOut


class AnswerOut(ORMModel):
    id: uuid.UUID
    question_external_id: str
    question_text: str
    question_type: QuestionType
    required: bool
    options: list[str]
    answer_value: str | None
    source_fact_id: uuid.UUID | None
    confidence: int
    needs_human: bool
    reason: str


class AnswerUpdateIn(BaseModel):
    answer_id: uuid.UUID
    answer_value: str
    source_fact_id: uuid.UUID | None = None


class ApplicationDocumentOut(ORMModel):
    id: uuid.UUID
    document_id: uuid.UUID
    role: str
    attached: bool


class ApplicationOut(ORMModel):
    id: uuid.UUID
    job_id: uuid.UUID
    match_id: uuid.UUID | None
    status: ApplicationStatus
    pipeline_stage: PipelineStage
    submission_policy: SubmissionPolicy
    version: int
    summary: str
    fact_guard_flags: list[dict]
    validation_errors: list[str]
    prefilled_fields: dict
    approved_at: datetime | None
    submitted_at: datetime | None
    confirmation_number: str | None
    last_error: str
    attempt_count: int
    created_at: datetime
    updated_at: datetime


class ApplicationDetailOut(ApplicationOut):
    job: JobOut
    answers: list[AnswerOut]
    documents: list[ApplicationDocumentOut]


class DraftIn(BaseModel):
    job_id: uuid.UUID
    include_cover_letter: bool = True


class DraftOut(BaseModel):
    application: ApplicationOut
    policy: dict
    review_task_id: uuid.UUID | None
    validation_errors: list[str]
    blocking_questions: list[dict]


class DecisionIn(BaseModel):
    note: str = Field(default="", max_length=1000)


class StageIn(BaseModel):
    pipeline_stage: Stage
    note: str = Field(default="", max_length=1000)


class MarkSubmittedIn(BaseModel):
    """You sent this application yourself and are recording that fact."""

    confirmation_number: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=1000)


class ReviewTaskOut(ORMModel):
    id: uuid.UUID
    application_id: uuid.UUID | None
    job_id: uuid.UUID | None
    reason: ReviewReason
    status: ReviewStatus
    title: str
    detail: str
    action_url: str
    draft_payload: dict
    blocking_questions: list[dict]
    resolved_at: datetime | None
    resolution_note: str
    created_at: datetime


class SubmissionAttemptOut(ORMModel):
    id: uuid.UUID
    attempt_number: int
    mode: str
    outcome: str
    started_at: datetime | None
    finished_at: datetime | None
    guard_findings: list[dict]
    filled_fields: list[dict]
    error_message: str
    assistant_version: str


class NotificationOut(ORMModel):
    id: uuid.UUID
    kind: NotificationKind
    channel: str
    title: str
    body: str
    link: str
    data: dict
    read_at: datetime | None
    created_at: datetime


class AuditOut(ORMModel):
    id: uuid.UUID
    seq: int
    created_at: datetime
    actor: str
    action: str
    object_type: str
    object_id: str
    outcome: str
    request_id: str
    payload: dict
    prev_hash: str
    entry_hash: str
