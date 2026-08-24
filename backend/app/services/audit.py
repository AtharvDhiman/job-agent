"""Append-only, hash-chained audit trail.

record() is the only writer. There is no update or delete path anywhere in the
codebase, and verify_chain() re-walks the chain so tampering is detectable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import AuditAction
from app.core.logging import get_logger, request_id_var
from app.models.audit import GENESIS_HASH, AuditLog

log = get_logger(__name__)


def record(
    db: Session,
    action: AuditAction | str,
    *,
    user_id: uuid.UUID | None = None,
    actor: str = "system",
    object_type: str = "",
    object_id: str = "",
    outcome: str = "ok",
    payload: dict[str, Any] | None = None,
    ip_address: str = "",
) -> AuditLog:
    stmt = select(AuditLog).order_by(AuditLog.seq.desc()).limit(1)
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        # Serialise concurrent writers so two entries cannot claim the same seq.
        stmt = stmt.with_for_update()
    previous = db.execute(stmt).scalar_one_or_none()
    entry = AuditLog(
        seq=(previous.seq + 1) if previous else 1,
        created_at=datetime.now(UTC),
        user_id=user_id,
        actor=actor,
        action=action.value if isinstance(action, AuditAction) else str(action),
        object_type=object_type,
        object_id=str(object_id or ""),
        outcome=outcome,
        request_id=request_id_var.get() or "",
        ip_address=ip_address,
        payload=_scrub(payload or {}),
        prev_hash=previous.entry_hash if previous else GENESIS_HASH,
    )
    entry.entry_hash = entry.compute_hash()
    db.add(entry)
    db.flush()
    log.info("audit", action=entry.action, object_type=object_type, object_id=str(object_id))
    return entry


_REDACT_KEYS = {
    "password",
    "token",
    "api_key",
    "secret",
    "authorization",
    "answer",
    "answer_value",
    "confirmation_number",
    "acknowledgement",
    "acknowledgement_text",
    "phone",
    "address",
    "ssn",
}

#: Same substring rule as the log redactor, so a field named e.g.
#: `assistant_token` is caught without having to be enumerated.
_REDACT_FRAGMENTS = ("password", "token", "secret", "api_key", "apikey", "credential")


def _is_secret(key: str) -> bool:
    lowered = str(key).lower()
    return lowered in _REDACT_KEYS or any(f in lowered for f in _REDACT_FRAGMENTS)


def _scrub_value(value):
    if isinstance(value, dict):
        return _scrub(value)
    # Lists were walked straight past, so a payload like
    # {"guard_findings": [{"answer_value": "..."}]} was written verbatim.
    if isinstance(value, (list, tuple)):
        return [_scrub_value(v) for v in value]
    return value


def _scrub(payload: dict) -> dict:
    out = {}
    for key, value in payload.items():
        if _is_secret(key):
            out[key] = "[redacted]"
        else:
            out[key] = _scrub_value(value)
    return out


def verify_chain(db: Session, limit: int | None = None) -> dict:
    """Re-walk the chain. Returns the first break, if any."""
    query = select(AuditLog).order_by(AuditLog.seq.asc())
    if limit:
        query = query.limit(limit)
    entries = list(db.execute(query).scalars())
    previous_hash = GENESIS_HASH
    for entry in entries:
        if entry.prev_hash != previous_hash:
            return {
                "valid": False,
                "checked": len(entries),
                "broken_at_seq": entry.seq,
                "detail": "prev_hash does not match the preceding entry",
            }
        if entry.entry_hash != entry.compute_hash():
            return {
                "valid": False,
                "checked": len(entries),
                "broken_at_seq": entry.seq,
                "detail": "entry contents do not match their recorded hash",
            }
        previous_hash = entry.entry_hash
    return {"valid": True, "checked": len(entries), "broken_at_seq": None, "detail": ""}
