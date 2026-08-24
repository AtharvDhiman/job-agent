"""Source tools: the board finder and the curated source catalog.

Both exist so the user never has to know what a "board token" is: they type a
company name or pick a curated feed, and adding the source is one click on a
candidate that was verified to exist.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DbSession, RequireOperator
from app.connectors import PoliteClient, registry
from app.connectors.catalog import CATALOG
from app.core.config import settings
from app.models.job import JobSourceSubscription
from app.schemas.source_tools import BoardSearchIn, BoardSearchOut, CatalogEntryOut, FoundBoard
from app.services import audit
from app.services.board_finder import find_boards

router = APIRouter(prefix="/sources", tags=["source-tools"])


def _subscribed_pairs(db: Session, user_id: uuid.UUID) -> set[tuple[str, str]]:
    """(connector_key, identifier) pairs this user already polls, so the UI can
    offer "add" only for sources that would not 409 on POST /sources."""
    rows = db.execute(
        select(JobSourceSubscription.connector_key, JobSourceSubscription.identifier).where(
            JobSourceSubscription.user_id == user_id
        )
    ).all()
    return {(key, identifier) for key, identifier in rows}


@router.post("/find", response_model=BoardSearchOut)
def find_company_boards(
    payload: BoardSearchIn, db: DbSession, user: RequireOperator
) -> BoardSearchOut:
    """Probe the five documented public job-board APIs for a company's board.

    Operator-gated like POST /sources: this endpoint makes outbound requests
    on the user's behalf, even though it writes nothing.
    """
    # A fresh client per search: the endpoint must not hold sockets open
    # between requests, and PoliteClient's per-host throttle still applies
    # within the search itself.
    with PoliteClient() as client:
        candidates = find_boards(payload.company, client=client)

    subscribed = _subscribed_pairs(db, user.id)
    found = [
        FoundBoard(
            **candidate,
            already_added=(candidate["connector_key"], candidate["identifier"]) in subscribed,
        )
        for candidate in candidates
    ]
    audit.record(
        db,
        "sources.board_search",
        user_id=user.id,
        actor=user.email,
        object_type="job_source_subscription",
        payload={"company": payload.company, "found": len(found)},
    )
    return BoardSearchOut(company=payload.company, candidates=found)


@router.get("/catalog", response_model=list[CatalogEntryOut])
def source_catalog(db: DbSession, user: CurrentUser) -> list[CatalogEntryOut]:
    """The curated starting-point sources, flagged with what this user can do.

    `available` mirrors the connector's own credential check so the UI never
    offers a one-click add that discovery would immediately refuse to run.
    """
    subscribed = _subscribed_pairs(db, user.id)
    entries: list[CatalogEntryOut] = []
    for entry in CATALOG:
        available = True
        if entry["requires_credentials"]:
            available, _reason = registry.get(entry["connector_key"]).is_available(settings)
        entries.append(
            CatalogEntryOut(
                **entry,
                already_added=(entry["connector_key"], entry["identifier"]) in subscribed,
                available=available,
            )
        )
    return entries
