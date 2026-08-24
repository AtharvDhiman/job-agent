from __future__ import annotations

from pydantic import BaseModel, Field


class BoardSearchIn(BaseModel):
    company: str = Field(min_length=1, max_length=200)


class FoundBoard(BaseModel):
    connector_key: str
    identifier: str
    display_name: str
    url: str
    job_count: int
    probed_slug: str
    already_added: bool = False


class BoardSearchOut(BaseModel):
    company: str
    candidates: list[FoundBoard]


class CatalogEntryOut(BaseModel):
    connector_key: str
    identifier: str
    display_name: str
    note: str
    compliance_note: str
    requires_credentials: list[str]
    already_added: bool
    available: bool
