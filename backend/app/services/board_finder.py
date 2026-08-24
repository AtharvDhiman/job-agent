"""Find a company's job board from nothing but its name.

The user should not need to know what a "board token" is. They type a company
name; we derive the obvious slugs and probe the five documented, public
job-board APIs this codebase already integrates (Greenhouse, Lever, Ashby,
SmartRecruiters, Workable) to see which boards actually exist.

This module only ever probes those five documented public APIs -- the exact
endpoints the connectors themselves poll for subscribed sources. It must NOT
fetch arbitrary URLs, query search engines, or scrape any site's HTML; adding
a probe outside PROBES requires the same compliance review as a new connector.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.connectors.base import ConnectorError
from app.connectors.http import PoliteClient
from app.utils.text import normalize_company


def slug_candidates(company: str) -> list[str]:
    """Obvious board slugs for a company name, most likely first.

    normalize_company already folds case/accents and strips punctuation and
    legal suffixes (Inc, LLC, GmbH, ...), so "Northwind Systems, Inc." becomes
    the words ["northwind", "systems"] before we join them.
    """
    words = normalize_company(company).split()
    if not words:
        return []
    ordered = ["".join(words), "-".join(words), words[0]]
    candidates: list[str] = []
    for slug in ordered:
        if slug and slug not in candidates:
            candidates.append(slug)
    return candidates


def _parse_greenhouse(payload: Any) -> int | None:
    if isinstance(payload, dict) and "jobs" in payload:
        return len(payload["jobs"] or [])
    return None


def _parse_lever(payload: Any) -> int | None:
    return len(payload) if isinstance(payload, list) else None


def _parse_ashby(payload: Any) -> int | None:
    if isinstance(payload, dict) and "jobs" in payload:
        return len(payload.get("jobs") or [])
    return None


def _parse_smartrecruiters(payload: Any) -> int | None:
    if isinstance(payload, dict) and payload.get("totalFound") is not None:
        return int(payload["totalFound"])
    return None


def _parse_workable(payload: Any) -> int | None:
    if isinstance(payload, dict) and "jobs" in payload:
        return len(payload.get("jobs") or [])
    return None


@dataclass(frozen=True, slots=True)
class _Probe:
    connector_key: str
    #: The public API endpoint -- identical to the one the connector fetches.
    api_url: Callable[[str], str]
    #: The human-facing board page we show the user, not the API endpoint.
    board_url: Callable[[str], str]
    #: Payload -> job count, or None meaning "this slug is not this company".
    parse: Callable[[Any], int | None]


PROBES: tuple[_Probe, ...] = (
    _Probe(
        "greenhouse",
        lambda s: f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs",
        lambda s: f"https://boards.greenhouse.io/{s}",
        _parse_greenhouse,
    ),
    _Probe(
        "lever",
        lambda s: f"https://api.lever.co/v0/postings/{s}?mode=json",
        lambda s: f"https://jobs.lever.co/{s}",
        _parse_lever,
    ),
    _Probe(
        "ashby",
        lambda s: f"https://api.ashbyhq.com/posting-api/job-board/{s}",
        lambda s: f"https://jobs.ashbyhq.com/{s}",
        _parse_ashby,
    ),
    _Probe(
        "smartrecruiters",
        lambda s: f"https://api.smartrecruiters.com/v1/companies/{s}/postings?limit=1",
        lambda s: f"https://careers.smartrecruiters.com/{s}",
        _parse_smartrecruiters,
    ),
    _Probe(
        "workable",
        lambda s: f"https://apply.workable.com/api/v1/widget/accounts/{s}",
        lambda s: f"https://apply.workable.com/{s}",
        _parse_workable,
    ),
)


def _probe_one(http: PoliteClient, probe: _Probe, slug: str) -> int | None:
    """One GET against one platform. A miss is normal, never an error.

    These endpoints 404 for unknown slugs, and get_json surfaces any >=400 as
    ConnectorError -- so ConnectorError here just means "not this company".
    BlockedByPolicyError (a ConnectorError subclass: bot walls, 403s) means
    "we will not look here", which for a speculative probe is the same answer.
    A probe must never propagate an exception: the whole search should still
    return whatever the other platforms found.
    """
    try:
        payload, _etag = http.get_json(probe.api_url(slug), check_robots=False)
    except ConnectorError:
        return None
    try:
        return probe.parse(payload)
    except (TypeError, ValueError):
        # A payload that exists but does not parse is some other product's
        # page at the same path, not this company's board.
        return None


def find_boards(
    company: str, *, client: PoliteClient | None = None, max_slugs: int = 2
) -> list[dict]:
    """Probe every platform for each slug candidate; return the boards found.

    At most max_slugs * len(PROBES) requests, all to the same five public API
    hosts discovery already polls, and PoliteClient throttles them per host --
    so a search costs no more than one discovery tick against those hosts.
    """
    slugs = slug_candidates(company)[:max_slugs]
    if not slugs:
        return []

    owns_client = client is None
    http = client if client is not None else PoliteClient()
    found: set[str] = set()
    results: list[dict] = []
    try:
        for slug in slugs:
            for probe in PROBES:
                # One hit per platform is enough: a second slug matching the
                # same platform is almost always an unrelated company.
                if probe.connector_key in found:
                    continue
                job_count = _probe_one(http, probe, slug)
                if job_count is None:
                    continue
                found.add(probe.connector_key)
                results.append(
                    {
                        "connector_key": probe.connector_key,
                        "identifier": slug,
                        "display_name": company,
                        "url": probe.board_url(slug),
                        "job_count": job_count,
                        "probed_slug": slug,
                    }
                )
    finally:
        if owns_client:
            http.close()
    return results
