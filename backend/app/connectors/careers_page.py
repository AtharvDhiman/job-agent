"""A company's own careers page, read via structured data only.

Discovery: CAREERS_PAGE. We fetch the page ONLY if robots.txt allows it, and we
read ONLY schema.org JobPosting JSON-LD -- the machine-readable block publishers
add precisely so search engines and aggregators can index the role. We do not
reverse-engineer private APIs, and any bot-check aborts the run.
Submission: REVIEW_REQUIRED. Bespoke forms always go to a human.
"""

from __future__ import annotations

import json

from selectolax.parser import HTMLParser

from app.connectors.base import (
    BaseConnector,
    BlockedByPolicyError,
    ConnectorError,
    FetchResult,
    RawJob,
    SourceSpec,
    registry,
)
from app.core.enums import ComplianceTier, SubmissionPolicy
from app.utils.text import normalize_ws, parse_datetime, strip_html


@registry.register
class CareersPageConnector(BaseConnector):
    key = "careers_page"
    display_name = "Company careers page"
    compliance_tier = ComplianceTier.CAREERS_PAGE
    submission_policy_default = SubmissionPolicy.REVIEW_REQUIRED
    policy_note = (
        "Fetched only when robots.txt permits, at a polite rate, reading only "
        "schema.org JobPosting structured data. A bot-check, login wall or "
        "Disallow rule stops the run and records blocked_by_policy."
    )
    identifier_label = "Careers page URL"
    identifier_help = "Full https:// URL of a careers page or a single job posting."

    def fetch(self, spec: SourceSpec, *, etag: str = "") -> FetchResult:
        url = spec.identifier.strip()
        if not url.startswith("https://"):
            raise ConnectorError("Careers page URL must be https://")

        resp = self.http.get(url, etag=etag)  # raises BlockedByPolicyError on robots/bot wall
        if resp.status_code == 304:
            return FetchResult(jobs=[], etag=etag, notes=["not modified"])

        postings = _extract_job_postings(resp.text)
        if not postings:
            raise BlockedByPolicyError(
                f"{url} publishes no schema.org JobPosting data. We will not scrape the page "
                "layout. Add this company's ATS board instead, or add the job manually."
            )

        company_default = spec.display_name or spec.config.get("company", "")
        jobs = []
        for index, posting in enumerate(postings):
            title = normalize_ws(posting.get("title") or "")
            if not title:
                continue
            body = posting.get("description") or ""
            hiring_org = posting.get("hiringOrganization") or {}
            org_name = (
                hiring_org.get("name") if isinstance(hiring_org, dict) else str(hiring_org)
            ) or company_default
            direct_url = posting.get("url") or posting.get("sameAs") or url
            identifier = posting.get("identifier")
            if isinstance(identifier, dict):
                identifier = identifier.get("value")
            jobs.append(
                RawJob(
                    external_id=str(identifier or f"{url}#{index}")[:280],
                    title=title,
                    company=normalize_ws(str(org_name)),
                    source_url=direct_url,
                    apply_url=posting.get("applicationUrl") or direct_url,
                    description_html=body,
                    description_text=strip_html(body),
                    location_raw=_location(posting),
                    employment_type=_first(posting.get("employmentType")),
                    posted_at=parse_datetime(posting.get("datePosted")),
                    deadline_at=parse_datetime(posting.get("validThrough")),
                    remote_flag=_remote(posting),
                    salary_min=_salary(posting, "minValue"),
                    salary_max=_salary(posting, "maxValue"),
                    salary_currency=str(
                        (posting.get("baseSalary") or {}).get("currency") or ""
                    ).upper()[:3],
                    salary_period=_period(posting),
                    is_direct_employer=True,
                    raw={"page": url, "source": "json-ld"},
                )
            )
        return FetchResult(jobs=jobs, etag=resp.headers.get("ETag", ""))


def _extract_job_postings(html: str) -> list[dict]:
    """Pull every schema.org JobPosting out of the page's JSON-LD blocks."""
    tree = HTMLParser(html)
    found: list[dict] = []
    for node in tree.css('script[type="application/ld+json"]'):
        raw = node.text(strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        _collect(data, found)
    return found


def _collect(data, found: list[dict], depth: int = 0) -> None:
    if depth > 6:
        return
    if isinstance(data, list):
        for item in data:
            _collect(item, found, depth + 1)
        return
    if not isinstance(data, dict):
        return
    types = data.get("@type")
    types = types if isinstance(types, list) else [types]
    if any(str(t).lower() == "jobposting" for t in types if t):
        found.append(data)
        return
    for key in ("@graph", "itemListElement", "item", "mainEntity"):
        if key in data:
            _collect(data[key], found, depth + 1)


def _first(value) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def _location(posting: dict) -> str:
    if posting.get("jobLocationType") == "TELECOMMUTE":
        applicant = posting.get("applicantLocationRequirements") or {}
        name = applicant.get("name") if isinstance(applicant, dict) else None
        return f"Remote ({name})" if name else "Remote"
    loc = posting.get("jobLocation")
    loc = loc[0] if isinstance(loc, list) and loc else loc
    address = (loc or {}).get("address") if isinstance(loc, dict) else None
    if not isinstance(address, dict):
        return ""
    parts = [
        address.get("addressLocality"),
        address.get("addressRegion"),
        address.get("addressCountry")
        if isinstance(address.get("addressCountry"), str)
        else (address.get("addressCountry") or {}).get("name"),
    ]
    return ", ".join(str(p) for p in parts if p)


def _remote(posting: dict) -> bool | None:
    if posting.get("jobLocationType") == "TELECOMMUTE":
        return True
    return None


def _salary(posting: dict, field: str) -> int | None:
    value = ((posting.get("baseSalary") or {}).get("value") or {}).get(field)
    return int(value) if isinstance(value, (int, float)) else None


def _period(posting: dict) -> str:
    unit = str(((posting.get("baseSalary") or {}).get("value") or {}).get("unitText") or "").upper()
    return {"YEAR": "year", "MONTH": "month", "HOUR": "hour", "WEEK": "week"}.get(unit, "")
