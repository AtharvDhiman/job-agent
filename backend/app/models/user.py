from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import SubmissionPolicy
from app.core.security import Role
from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.db.types import GUID, EncryptedString, JSONType


class User(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(20), default=Role.OWNER.value, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    profile = relationship(
        "CandidateProfile", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    settings = relationship(
        "AgentSettings", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class RefreshToken(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Set ONLY when this token was revoked by being rotated -- never by logout
    #: or by a family-wide revocation. That distinction is what lets /auth/refresh
    #: tell a concurrency race (two tabs spending one rotated token) apart from a
    #: replay of a token that was already killed for another reason.
    replaced_by_jti: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str] = mapped_column(String(300), default="")


class AgentSettings(UUIDPrimaryKey, Timestamps, Base):
    """Per-user automation configuration. The kill-switch lives here."""

    __tablename__ = "agent_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    automation_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    paused_reason: Mapped[str] = mapped_column(String(300), default="")
    auto_submit_min_score: Mapped[int] = mapped_column(Integer, default=85, nullable=False)
    daily_application_limit: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    job_max_age_hours: Mapped[int] = mapped_column(Integer, default=48, nullable=False)
    discovery_interval_minutes: Mapped[int] = mapped_column(Integer, default=180, nullable=False)
    shortlist_min_score: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    notify_channels: Mapped[dict] = mapped_column(
        JSONType, default=lambda: {"in_app": True, "email": False}
    )
    digest_hour_local: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)

    user = relationship("User", back_populates="settings")


class PlatformAuthorization(UUIDPrimaryKey, Timestamps, Base):
    """Explicit, typed consent to automate one platform. See docs/COMPLIANCE.md."""

    __tablename__ = "platform_authorizations"
    __table_args__ = (UniqueConstraint("user_id", "platform_key", name="uq_platform_auth"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    platform_key: Mapped[str] = mapped_column(String(64), nullable=False)
    policy: Mapped[str] = mapped_column(
        String(32), default=SubmissionPolicy.REVIEW_REQUIRED.value, nullable=False
    )
    acknowledgement_text: Mapped[str | None] = mapped_column(EncryptedString)
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str] = mapped_column(String(500), default="")

    @property
    def is_active(self) -> bool:
        return self.granted_at is not None and self.revoked_at is None
