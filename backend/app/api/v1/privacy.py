"""Data export and erasure (GDPR-style rights, implemented literally)."""

from __future__ import annotations

import json
from base64 import b64encode
from datetime import UTC, datetime

from fastapi import APIRouter, Response
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, RequireOwner
from app.core.enums import AuditAction
from app.models.application import (
    Application,
    ApplicationAnswer,
    ApplicationDocument,
    ReviewTask,
    SubmissionAttempt,
)
from app.models.audit import AuditLog, DailyCounter, Notification
from app.models.job import JobMatch, JobSourceSubscription
from app.models.profile import CandidateProfile, CareerFact, Document
from app.models.user import AgentSettings, PlatformAuthorization, RefreshToken
from app.schemas.settings import EraseIn
from app.services import audit, storage

router = APIRouter(prefix="/privacy", tags=["privacy"])


def _dump(row) -> dict:
    out = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        if isinstance(value, datetime):
            out[column.name] = value.isoformat()
        elif hasattr(value, "hex") and not isinstance(value, (bytes, bytearray)):
            out[column.name] = str(value)
        else:
            out[column.name] = value
    return out


def _rows(db, model, condition):
    return [_dump(r) for r in db.execute(select(model).where(condition)).scalars()]


#: Ceiling on the bytes of stored files inlined into one export, so a large
#: document library cannot turn a GDPR request into an out-of-memory response.
MAX_EXPORT_FILE_BYTES = 64 * 1024 * 1024


def _export_files(db, user_id) -> tuple[list[dict], list[dict]]:
    """Every stored file the user owns, base64 encoded, plus what was skipped.

    docs/COMPLIANCE.md promises the export carries "the stored files", not just
    rows describing them; before this it carried metadata only.
    """
    backend = storage.get_storage()
    files: list[dict] = []
    omitted: list[dict] = []
    budget = MAX_EXPORT_FILE_BYTES

    for document in db.execute(
        select(Document).where(Document.user_id == user_id).order_by(Document.created_at)
    ).scalars():
        entry = {
            "document_id": str(document.id),
            "filename": document.filename,
            "content_type": document.content_type,
            "sha256": document.sha256,
            "size_bytes": document.size_bytes,
        }
        try:
            content = backend.read(document.storage_key)
        except (FileNotFoundError, OSError, ValueError) as exc:
            omitted.append({**entry, "reason": f"stored file unreadable: {exc}"})
            continue
        if len(content) > budget:
            omitted.append(
                {
                    **entry,
                    "reason": (
                        "omitted to keep the export under "
                        f"{MAX_EXPORT_FILE_BYTES // (1024 * 1024)}MB; download it from "
                        f"/api/v1/documents/{document.id}/content"
                    ),
                }
            )
            continue
        budget -= len(content)
        files.append({**entry, "encoding": "base64", "content": b64encode(content).decode()})

    return files, omitted


@router.get("/export")
def export_data(db: DbSession, user: CurrentUser) -> Response:
    """Everything we hold about you, as a downloadable JSON file."""
    profile = db.execute(
        select(CandidateProfile).where(CandidateProfile.user_id == user.id)
    ).scalar_one_or_none()
    applications = list(
        db.execute(select(Application).where(Application.user_id == user.id)).scalars()
    )
    application_ids = [a.id for a in applications]
    stored_files, omitted_files = _export_files(db, user.id)

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "user": {**_dump(user), "hashed_password": "[not exported]"},
        "profile": _dump(profile) if profile else None,
        "agent_settings": _rows(db, AgentSettings, AgentSettings.user_id == user.id),
        "platform_authorizations": _rows(
            db, PlatformAuthorization, PlatformAuthorization.user_id == user.id
        ),
        "career_facts": _rows(db, CareerFact, CareerFact.profile_id == profile.id)
        if profile
        else [],
        "documents": _rows(db, Document, Document.user_id == user.id),
        "stored_files": stored_files,
        "stored_files_omitted": omitted_files,
        "job_sources": _rows(db, JobSourceSubscription, JobSourceSubscription.user_id == user.id),
        "matches": _rows(db, JobMatch, JobMatch.user_id == user.id),
        "applications": [_dump(a) for a in applications],
        "application_answers": _rows(
            db, ApplicationAnswer, ApplicationAnswer.application_id.in_(application_ids)
        )
        if application_ids
        else [],
        "submission_attempts": _rows(
            db, SubmissionAttempt, SubmissionAttempt.application_id.in_(application_ids)
        )
        if application_ids
        else [],
        "review_tasks": _rows(db, ReviewTask, ReviewTask.user_id == user.id),
        "notifications": _rows(db, Notification, Notification.user_id == user.id),
        "audit_log": [
            _dump(e)
            for e in db.execute(
                select(AuditLog).where(AuditLog.user_id == user.id).order_by(AuditLog.seq)
            ).scalars()
        ],
    }
    audit.record(
        db,
        AuditAction.DATA_EXPORTED,
        user_id=user.id,
        actor=user.email,
        object_type="user",
        object_id=str(user.id),
        payload={"records": {k: len(v) for k, v in payload.items() if isinstance(v, list)}},
    )
    return Response(
        content=json.dumps(payload, indent=2, default=str),
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="jobagent-export.json"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/erase", response_model=dict)
def erase_data(payload: EraseIn, db: DbSession, user: RequireOwner) -> dict:
    """Irreversibly delete personal data.

    Audit entries are ANONYMISED rather than deleted: their hash chain must stay
    intact for the trail to remain verifiable, so we null the user link and
    scrub the identifying fields instead of removing rows.
    """
    profile = db.execute(
        select(CandidateProfile).where(CandidateProfile.user_id == user.id)
    ).scalar_one_or_none()

    backend = storage.get_storage()
    files_removed = 0
    for document in db.execute(select(Document).where(Document.user_id == user.id)).scalars():
        try:
            backend.delete(document.storage_key)
            files_removed += 1
        except OSError:
            pass

    application_ids = [
        a.id
        for a in db.execute(select(Application).where(Application.user_id == user.id)).scalars()
    ]
    deleted: dict[str, int] = {}

    def wipe(model, condition) -> None:
        result = db.execute(model.__table__.delete().where(condition))
        deleted[model.__tablename__] = result.rowcount or 0

    if application_ids:
        wipe(SubmissionAttempt, SubmissionAttempt.application_id.in_(application_ids))
        wipe(ApplicationAnswer, ApplicationAnswer.application_id.in_(application_ids))
        wipe(ApplicationDocument, ApplicationDocument.application_id.in_(application_ids))
    wipe(ReviewTask, ReviewTask.user_id == user.id)
    wipe(Application, Application.user_id == user.id)
    wipe(JobMatch, JobMatch.user_id == user.id)
    wipe(JobSourceSubscription, JobSourceSubscription.user_id == user.id)
    wipe(Notification, Notification.user_id == user.id)
    wipe(DailyCounter, DailyCounter.user_id == user.id)
    wipe(Document, Document.user_id == user.id)
    if profile is not None:
        wipe(CareerFact, CareerFact.profile_id == profile.id)
        wipe(CandidateProfile, CandidateProfile.id == profile.id)
    wipe(PlatformAuthorization, PlatformAuthorization.user_id == user.id)
    wipe(AgentSettings, AgentSettings.user_id == user.id)
    wipe(RefreshToken, RefreshToken.user_id == user.id)

    anonymised = 0
    for entry in db.execute(select(AuditLog).where(AuditLog.user_id == user.id)).scalars():
        entry.user_id = None
        entry.actor = "erased-user"
        entry.ip_address = ""
        anonymised += 1

    erased_id = str(user.id)
    user.is_active = False
    user.email = f"erased-{erased_id}@invalid"
    user.full_name = ""
    # Not a password: no Argon2 hash starts with "!", so this can never verify.
    user.hashed_password = "!erased"  # noqa: S105
    db.flush()

    audit.record(
        db,
        AuditAction.DATA_ERASED,
        actor="erased-user",
        object_type="user",
        object_id=erased_id,
        payload={
            "rows_deleted": deleted,
            "files_removed": files_removed,
            "audit_entries_anonymised": anonymised,
        },
    )
    return {
        "erased": True,
        "rows_deleted": deleted,
        "files_removed": files_removed,
        "audit_entries_anonymised": anonymised,
        "note": (
            "Audit entries were anonymised, not deleted, so the tamper-evident hash chain "
            "remains verifiable."
        ),
    }
