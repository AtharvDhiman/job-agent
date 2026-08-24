"""Lever postings API.

Discovery: PUBLIC_JOB_API (api.lever.co/v0/postings/<site>?mode=json is the
documented public feed used to embed a company's own board).
Submission: REVIEW_REQUIRED by default; Lever-hosted forms may be authorized.
"""

from __future__ import annotations

from app.connectors.base import (
    BaseConnector,
    ConnectorError,
    FetchResult,
    RawJob,
    SourceSpec,
    registry,
)
from app.core.enums import ComplianceTier, SubmissionPolicy
from app.utils.text import parse_datetime, strip_html

BASE = "https://api.lever.co/v0/postings"


@registry.register
class LeverConnector(BaseConnector):
    key = "lever"
    display_name = "Lever"
    compliance_tier = ComplianceTier.PUBLIC_JOB_API
    submission_policy_default = SubmissionPolicy.REVIEW_REQUIRED
    policy_note = (
        "Documented public postings feed. Submission requires your explicit "
        "per-platform authorization; otherwise every application is queued for review."
    )
    identifier_label = "Lever site name"
    identifier_help = "The slug in jobs.lever.co/<site>, e.g. 'examplecorp'."

    def fetch(self, spec: SourceSpec, *, etag: str = "") -> FetchResult:
        site = spec.identifier.strip().strip("/")
        if not site:
            raise ConnectorError("Lever site name is required")
        url = f"{BASE}/{site}?mode=json"
        payload, new_etag = self.http.get_json(url, etag=etag, check_robots=False)
        if payload is None:
            return FetchResult(jobs=[], etag=etag, notes=["not modified"])
        if not isinstance(payload, list):
            raise ConnectorError(f"Unexpected Lever payload for site '{site}'")

        jobs = []
        for item in payload:
            categories = item.get("categories") or {}
            description_html = item.get("description") or ""
            extras = "".join(
                f"<h3>{block.get('text', '')}</h3>{block.get('content', '')}"
                for block in (item.get("lists") or [])
            )
            full_html = description_html + extras + (item.get("additional") or "")
            workplace = (item.get("workplaceType") or "").lower()
            jobs.append(
                RawJob(
                    external_id=f"{site}:{item.get('id')}",
                    title=(item.get("text") or "").strip(),
                    company=spec.display_name or spec.config.get("company") or site,
                    source_url=item.get("hostedUrl", ""),
                    apply_url=item.get("applyUrl") or item.get("hostedUrl", ""),
                    description_html=full_html,
                    description_text=item.get("descriptionPlain") or strip_html(full_html),
                    location_raw=categories.get("location", "") or "",
                    department=categories.get("department", "") or categories.get("team", "") or "",
                    employment_type=categories.get("commitment", "") or "",
                    posted_at=parse_datetime(item.get("createdAt")),
                    remote_flag=True if workplace == "remote" else (False if workplace else None),
                    is_direct_employer=True,
                    raw={"id": item.get("id"), "site": site, "workplaceType": workplace},
                )
            )
        return FetchResult(jobs=jobs, etag=new_etag or etag)
