from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import NotificationChannel, NotificationKind
from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.db.types import GUID, JSONType

GENESIS_HASH = "0" * 64


class AuditLog(UUIDPrimaryKey, Base):
    """Append-only, hash-chained. There is no update or delete route.

    entry_hash = sha256(prev_hash | seq | ts | actor | action | object | payload)
    so any retro-edit breaks the chain and GET /audit/verify reports it.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_user_time", "user_id", "created_at"),
        Index("ix_audit_action", "action"),
    )

    #: Monotonic chain position. Assigned by services.audit.record() under a row
    #: lock rather than by a dialect sequence, so the chain is identical on every
    #: database and a concurrent insert collides on the unique constraint instead
    #: of silently forking the chain.
    seq: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        autoincrement=False,
        unique=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL")
    )
    actor: Mapped[str] = mapped_column(String(120), default="system")
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), default="")
    object_id: Mapped[str] = mapped_column(String(64), default="")
    outcome: Mapped[str] = mapped_column(String(24), default="ok")
    request_id: Mapped[str] = mapped_column(String(64), default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), default=GENESIS_HASH, nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    def compute_hash(self) -> str:
        """Chain material.

        `actor`, `user_id` and `ip_address` are deliberately EXCLUDED. Erasure
        must be able to scrub identifying fields without destroying the chain,
        so the chain covers what happened -- action, object, outcome, payload,
        position and time -- rather than who the row names. Those three fields
        are the only mutable ones, and the database trigger enforces that.

        The timestamp is normalised to naive UTC so a driver that returns
        timezone-naive datetimes (SQLite) hashes identically to one that does
        not (PostgreSQL).
        """
        stamp = self.created_at
        if stamp is not None and stamp.tzinfo is not None:
            stamp = stamp.astimezone(UTC).replace(tzinfo=None)
        material = json.dumps(
            {
                "prev": self.prev_hash,
                "seq": self.seq,
                "ts": stamp.isoformat(timespec="microseconds") if stamp else "",
                "action": self.action,
                "object_type": self.object_type,
                "object_id": self.object_id,
                "outcome": self.outcome,
                "payload": self.payload or {},
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(material.encode()).hexdigest()


class Notification(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_unread", "user_id", "read_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(
        String(32), default=NotificationKind.SYSTEM_ALERT.value, nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(
        String(16), default=NotificationChannel.IN_APP.value, nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String(1000), default="")
    data: Mapped[dict] = mapped_column(JSONType, default=dict)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_error: Mapped[str] = mapped_column(String(1000), default="")


class DailyCounter(UUIDPrimaryKey, Timestamps, Base):
    """Enforces the daily application limit atomically."""

    __tablename__ = "daily_counters"
    __table_args__ = (Index("ix_daily_counter", "user_id", "day", "name", unique=True),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    day: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(48), default="applications_submitted")
    value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    frozen: Mapped[bool] = mapped_column(Boolean, default=False)
