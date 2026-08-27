from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import (
    ApplicationStatus,
    PipelineStage,
    QuestionType,
    ReviewStatus,
    SubmissionPolicy,
)
from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.db.types import GUID, EncryptedString, JSONType


class Application(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_application_user_job"),
        Index("ix_applications_user_status", "user_id", "status"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    match_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("job_matches.id", ondelete="SET NULL")
    )

    status: Mapped[str] = mapped_column(
        String(32), default=ApplicationStatus.DRAFTING.value, nullable=False, index=True
    )
    pipeline_stage: Mapped[str] = mapped_column(
        String(20), default=PipelineStage.SAVED.value, nullable=False, index=True
    )
    submission_policy: Mapped[str] = mapped_column(
        String(32), default=SubmissionPolicy.REVIEW_REQUIRED.value, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    fact_guard_flags: Mapped[list] = mapped_column(JSONType, default=list)
    validation_errors: Mapped[list] = mapped_column(JSONType, default=list)
    #: Advisory quality report per document role ("resume", "cover_letter").
    #: Never blocks -- fact_guard_flags is the blocking one. See document_critic.
    critique: Mapped[dict] = mapped_column(JSONType, default=dict)
    prefilled_fields: Mapped[dict] = mapped_column(JSONType, default=dict)

    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmation_number: Mapped[str | None] = mapped_column(EncryptedString)
    submission_receipt: Mapped[dict] = mapped_column(JSONType, default=dict)
    screenshot_document_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("documents.id", ondelete="SET NULL")
    )
    last_error: Mapped[str] = mapped_column(String(2000), default="")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    documents = relationship(
        "ApplicationDocument", back_populates="application", cascade="all, delete-orphan"
    )
    answers = relationship(
        "ApplicationAnswer", back_populates="application", cascade="all, delete-orphan"
    )
    attempts = relationship(
        "SubmissionAttempt", back_populates="application", cascade="all, delete-orphan"
    )
    reviews = relationship("ReviewTask", back_populates="application", cascade="all, delete-orphan")


class ApplicationDocument(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "application_documents"

    application_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("applications.id", ondelete="CASCADE"), index=True, nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), default="resume")
    attached: Mapped[bool] = mapped_column(Boolean, default=True)

    application = relationship("Application", back_populates="documents")


class ApplicationAnswer(UUIDPrimaryKey, Timestamps, Base):
    """A screening answer. `source_fact_id` is mandatory for auto-submit."""

    __tablename__ = "application_answers"

    application_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("applications.id", ondelete="CASCADE"), index=True, nullable=False
    )
    question_external_id: Mapped[str] = mapped_column(String(300), default="")
    question_text: Mapped[str] = mapped_column(Text, default="")
    question_type: Mapped[str] = mapped_column(String(20), default=QuestionType.UNKNOWN.value)
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    options: Mapped[list] = mapped_column(JSONType, default=list)
    answer_value: Mapped[str | None] = mapped_column(EncryptedString)
    source_fact_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("career_facts.id", ondelete="SET NULL")
    )
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    needs_human: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reason: Mapped[str] = mapped_column(String(300), default="")

    application = relationship("Application", back_populates="answers")


class ReviewTask(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "review_tasks"
    __table_args__ = (Index("ix_review_open", "user_id", "status"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("jobs.id", ondelete="CASCADE")
    )
    reason: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(16), default=ReviewStatus.OPEN.value, nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    action_url: Mapped[str] = mapped_column(String(1000), default="")
    draft_payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    blocking_questions: Mapped[list] = mapped_column(JSONType, default=list)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str] = mapped_column(String(1000), default="")

    application = relationship("Application", back_populates="reviews")


class SubmissionAttempt(UUIDPrimaryKey, Timestamps, Base):
    """One run of the browser assistant or API submitter. Never deleted."""

    __tablename__ = "submission_attempts"
    #: Belt and braces behind the conditional claim in v1/assistant.next_task: if
    #: two pollers ever did race past it, the second insert collides here instead
    #: of producing two live attempts for one application.
    __table_args__ = (
        UniqueConstraint("application_id", "attempt_number", name="uq_attempt_number"),
    )

    application_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("applications.id", ondelete="CASCADE"), index=True, nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    mode: Mapped[str] = mapped_column(String(32), default="assisted_autofill")
    outcome: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    guard_findings: Mapped[list] = mapped_column(JSONType, default=list)
    filled_fields: Mapped[list] = mapped_column(JSONType, default=list)
    error_message: Mapped[str] = mapped_column(String(2000), default="")
    screenshot_document_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("documents.id", ondelete="SET NULL")
    )
    assistant_version: Mapped[str] = mapped_column(String(32), default="")

    application = relationship("Application", back_populates="attempts")
