from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, RequireOperator
from app.core.enums import AuditAction, DocumentKind
from app.models.profile import CandidateProfile, CareerFact, Document
from app.schemas.profile import (
    CareerFactIn,
    CareerFactOut,
    DocumentOut,
    ProfileIn,
    ProfileOut,
    UploadResult,
    VerifyFactsIn,
)
from app.services import audit, autopilot, resume_parser, storage

router = APIRouter(tags=["profile"])


def _profile_or_404(db, user) -> CandidateProfile:
    profile = db.execute(
        select(CandidateProfile).where(CandidateProfile.user_id == user.id)
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found")
    return profile


@router.get("/profile", response_model=ProfileOut)
def get_profile(db: DbSession, user: CurrentUser) -> CandidateProfile:
    return _profile_or_404(db, user)


@router.put("/profile", response_model=ProfileOut)
def upsert_profile(payload: ProfileIn, db: DbSession, user: RequireOperator) -> CandidateProfile:
    profile = db.execute(
        select(CandidateProfile).where(CandidateProfile.user_id == user.id)
    ).scalar_one_or_none()
    if profile is None:
        profile = CandidateProfile(user_id=user.id)
        db.add(profile)

    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        if key in ("work_arrangement_preference",):
            value = [v.value if hasattr(v, "value") else v for v in value]
        if key == "seniority_level" and hasattr(value, "value"):
            value = value.value
        setattr(profile, key, value)
    db.flush()
    audit.record(
        db,
        AuditAction.PROFILE_UPDATED,
        user_id=user.id,
        actor=user.email,
        object_type="candidate_profile",
        object_id=str(profile.id),
        payload={"fields": sorted(data)},
    )
    return profile


# ---------------------------------------------------------------- career facts
@router.get("/facts", response_model=list[CareerFactOut])
def list_facts(
    db: DbSession, user: CurrentUser, verified: bool | None = None, category: str | None = None
) -> list[CareerFact]:
    profile = _profile_or_404(db, user)
    stmt = select(CareerFact).where(CareerFact.profile_id == profile.id)
    if verified is not None:
        stmt = stmt.where(CareerFact.verified.is_(verified))
    if category:
        stmt = stmt.where(CareerFact.category == category)
    return list(db.execute(stmt.order_by(CareerFact.created_at.desc())).scalars())


@router.post("/facts", response_model=CareerFactOut, status_code=status.HTTP_201_CREATED)
def create_fact(payload: CareerFactIn, db: DbSession, user: RequireOperator) -> CareerFact:
    """Facts created by hand are still UNVERIFIED until explicitly verified."""
    profile = _profile_or_404(db, user)
    fact = CareerFact(
        profile_id=profile.id,
        **{**payload.model_dump(), "category": payload.category.value},
    )
    db.add(fact)
    db.flush()
    audit.record(
        db,
        AuditAction.FACT_CREATED,
        user_id=user.id,
        actor=user.email,
        object_type="career_fact",
        object_id=str(fact.id),
        payload={"category": fact.category, "verified": False},
    )
    return fact


@router.patch("/facts/{fact_id}", response_model=CareerFactOut)
def update_fact(
    fact_id: uuid.UUID, payload: CareerFactIn, db: DbSession, user: RequireOperator
) -> CareerFact:
    profile = _profile_or_404(db, user)
    fact = db.get(CareerFact, fact_id)
    if fact is None or fact.profile_id != profile.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fact not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(fact, key, value.value if hasattr(value, "value") else value)
    # Editing a fact invalidates its verification: you must confirm the new text.
    fact.verified = False
    fact.verified_at = None
    db.flush()
    return fact


@router.post("/facts/verify", response_model=list[CareerFactOut])
def verify_facts(payload: VerifyFactsIn, db: DbSession, user: RequireOperator) -> list[CareerFact]:
    """The human gate. Only verified facts can appear in an application."""
    profile = _profile_or_404(db, user)
    facts = list(
        db.execute(
            select(CareerFact).where(
                CareerFact.profile_id == profile.id, CareerFact.id.in_(payload.fact_ids)
            )
        ).scalars()
    )
    if not facts:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No matching facts")
    now = datetime.now(UTC)
    for fact in facts:
        fact.verified = payload.verified
        fact.verified_at = now if payload.verified else None
    db.flush()
    audit.record(
        db,
        AuditAction.FACT_VERIFIED,
        user_id=user.id,
        actor=user.email,
        object_type="career_fact",
        payload={
            "count": len(facts),
            "verified": payload.verified,
            "ids": [str(f.id) for f in facts],
        },
    )
    return facts


@router.delete("/facts/{fact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_fact(fact_id: uuid.UUID, db: DbSession, user: RequireOperator) -> None:
    profile = _profile_or_404(db, user)
    fact = db.get(CareerFact, fact_id)
    if fact is None or fact.profile_id != profile.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fact not found")
    db.delete(fact)
    audit.record(
        db,
        AuditAction.FACT_DELETED,
        user_id=user.id,
        actor=user.email,
        object_type="career_fact",
        object_id=str(fact_id),
    )


# ------------------------------------------------------------------ documents
@router.get("/documents", response_model=list[DocumentOut])
def list_documents(db: DbSession, user: CurrentUser, kind: str | None = None) -> list[Document]:
    stmt = select(Document).where(Document.user_id == user.id)
    if kind:
        stmt = stmt.where(Document.kind == kind)
    return list(db.execute(stmt.order_by(Document.created_at.desc())).scalars())


@router.post("/documents", response_model=UploadResult, status_code=status.HTTP_201_CREATED)
async def upload_document(
    db: DbSession,
    user: RequireOperator,
    file: UploadFile = File(...),
    kind: str = Form(default=DocumentKind.RESUME_SOURCE.value),
    label: str = Form(default=""),
    is_primary: bool = Form(default=False),
    propose_facts: bool = Form(default=True),
) -> UploadResult:
    # `kind` is a raw form field. It is used to build the storage key and is
    # persisted in a 32-char column, so it has to be a known DocumentKind and
    # not, say, "../../etc".
    if kind not in {k.value for k in DocumentKind}:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown document kind '{kind}'. Known: {sorted(k.value for k in DocumentKind)}",
        )
    content = await file.read()
    errors = storage.validate_upload(file.filename or "", content, file.content_type or "")
    if errors:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "; ".join(errors))

    digest = storage.sha256_of(content)
    existing = db.execute(
        select(Document).where(
            Document.user_id == user.id, Document.sha256 == digest, Document.kind == kind
        )
    ).scalar_one_or_none()

    parsed = resume_parser.ParsedResume()
    is_resume = kind in (DocumentKind.RESUME_SOURCE.value, DocumentKind.COVER_LETTER_SOURCE.value)
    if is_resume:
        parsed = resume_parser.parse_resume(content, file.filename or "", file.content_type or "")

    if existing is not None:
        return UploadResult(
            document=DocumentOut.model_validate(existing),
            proposed_fact_count=0,
            warnings=["Identical file already uploaded; kept the existing version."],
            parsed=existing.parsed or {},
        )

    key = storage.build_key(user.id, kind, digest, file.filename or "upload")
    storage.get_storage().write(key, content)

    previous = db.execute(
        select(Document)
        .where(Document.user_id == user.id, Document.kind == kind)
        .order_by(Document.version.desc())
        .limit(1)
    ).scalar_one_or_none()

    if is_primary:
        for row in db.execute(
            select(Document).where(Document.user_id == user.id, Document.kind == kind)
        ).scalars():
            row.is_primary = False

    document = Document(
        user_id=user.id,
        kind=kind,
        # Both columns are bounded in the schema (200 / 300); an over-long
        # multipart filename used to reach the driver and fail the INSERT.
        label=(label or (file.filename or ""))[:200],
        filename=(file.filename or "upload")[:300],
        content_type=(file.content_type or "application/octet-stream")[:120],
        storage_key=key,
        size_bytes=len(content),
        sha256=digest,
        version=(previous.version + 1) if previous else 1,
        parent_id=previous.id if previous else None,
        extracted_text=parsed.text,
        parsed=parsed.as_dict() if is_resume else {},
        is_primary=is_primary or previous is None,
    )
    db.add(document)
    db.flush()

    created_facts = 0
    auto_configured: dict[str, object] = {}
    if is_resume and propose_facts:
        profile = _profile_or_404(db, user)
        for proposed in parsed.proposed_facts:
            db.add(
                CareerFact(
                    profile_id=profile.id,
                    category=proposed.category,
                    key=proposed.key,
                    value=proposed.value,
                    organization=proposed.organization,
                    title=proposed.title,
                    start_date=proposed.start_date,
                    end_date=proposed.end_date,
                    is_current=proposed.is_current,
                    highlights=proposed.highlights,
                    evidence_url=proposed.evidence_url,
                    source_document_id=document.id,
                    verified=False,  # never auto-verified
                )
            )
            created_facts += 1
        db.flush()
        if kind == DocumentKind.RESUME_SOURCE.value:
            # Kick-start the pipeline: fill profile fields the human has left
            # empty so discovery and matching can work from the first upload.
            # Fields a human already set are never touched.
            auto_configured = autopilot.configure_from_resume(
                db, profile, parsed, parsed.proposed_facts
            )

    audit.record(
        db,
        AuditAction.DOCUMENT_UPLOADED,
        user_id=user.id,
        actor=user.email,
        object_type="document",
        object_id=str(document.id),
        payload={
            "kind": kind,
            "size": len(content),
            "sha256": digest,
            "proposed_facts": created_facts,
        },
    )
    warnings = list(parsed.warnings)
    if auto_configured:
        warnings.append(
            "Auto-filled empty profile fields from this resume: "
            + ", ".join(sorted(auto_configured))
            + ". You can change them under Profile."
        )
    return UploadResult(
        document=DocumentOut.model_validate(document),
        proposed_fact_count=created_facts,
        warnings=warnings,
        parsed=document.parsed or {},
        auto_configured=auto_configured,
    )


@router.get("/documents/{document_id}/content")
def download_document(document_id: uuid.UUID, db: DbSession, user: CurrentUser) -> Response:
    document = db.get(Document, document_id)
    if document is None or document.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    try:
        content = storage.get_storage().read(document.storage_key)
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(status.HTTP_410_GONE, f"Stored file is missing: {exc}") from None
    return Response(
        content=content,
        media_type=document.content_type,
        headers={
            # Never interpolate the stored filename raw: a quote or a CR/LF in it
            # would rewrite the header.
            "Content-Disposition": storage.content_disposition(document.filename),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: uuid.UUID, db: DbSession, user: RequireOperator) -> None:
    document = db.get(Document, document_id)
    if document is None or document.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    try:
        storage.get_storage().delete(document.storage_key)
    except OSError:
        pass
    db.delete(document)
