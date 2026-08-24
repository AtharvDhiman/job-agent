"""Ashby job board posting API.

Discovery: PUBLIC_JOB_API (api.ashbyhq.com/posting-api/job-board/<name>).
Submission: REVIEW_REQUIRED by default.
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

BASE = "https://api.ashbyhq.com/posting-api/job-board"


@registry.register
class AshbyConnector(BaseConnector):
    key = "ashby"
    display_name = "Ashby"
    compliance_tier = ComplianceTier.PUBLIC_JOB_API
    submission_policy_default = SubmissionPolicy.REVIEW_REQUIRED
    policy_note = (
        "Public posting API published by Ashby for embedding company boards. "
        "Submission is queued for review unless you authorize the platform."
    )
    identifier_label = "Job board name"
    identifier_help = "The slug in jobs.ashbyhq.com/<name>, e.g. 'examplecorp'."

    def fetch(self, spec: SourceSpec, *, etag: str = "") -> FetchResult:
        board = spec.identifier.strip().strip("/")
        if not board:
            raise ConnectorError("Ashby job board name is required")
        url = f"{BASE}/{board}?includeCompensation=true"
        payload, new_etag = self.http.get_json(url, etag=etag, check_robots=False)
        if payload is None:
            return FetchResult(jobs=[], etag=etag, notes=["not modified"])
        if not isinstance(payload, dict):
            raise ConnectorError(f"Unexpected Ashby payload for board '{board}'")

        jobs = []
        for item in payload.get("jobs") or []:
            if item.get("isListed") is False:
                continue
            html_desc = item.get("descriptionHtml") or ""
            comp = item.get("compensation") or {}
            salary_min, salary_max, currency, period = _compensation(comp)
            jobs.append(
                RawJob(
                    external_id=f"{board}:{item.get('id')}",
                    title=(item.get("title") or "").strip(),
                    company=payload.get("name") or spec.display_name or board,
                    source_url=item.get("jobUrl") or "",
                    apply_url=item.get("applyUrl") or item.get("jobUrl") or "",
                    description_html=html_desc,
                    description_text=item.get("descriptionPlain") or strip_html(html_desc),
                    location_raw=item.get("location") or "",
                    department=item.get("department") or item.get("team") or "",
                    employment_type=item.get("employmentType") or "",
                    posted_at=parse_datetime(item.get("publishedAt") or item.get("updatedAt")),
                    remote_flag=item.get("isRemote"),
                    salary_min=salary_min,
                    salary_max=salary_max,
                    salary_currency=currency,
                    salary_period=period,
                    is_direct_employer=True,
                    raw={"id": item.get("id"), "board": board, "compensation": comp or None},
                )
            )
        return FetchResult(jobs=jobs, etag=new_etag or etag)


def _compensation(comp: dict) -> tuple[int | None, int | None, str, str]:
    """Ashby nests salary under compensationTiers[].components[]."""
    for tier in comp.get("compensationTiers") or []:
        for component in tier.get("components") or []:
            if (component.get("compensationType") or "").lower() != "salary":
                continue
            low, high = component.get("minValue"), component.get("maxValue")
            currency = (component.get("currencyCode") or "").upper()[:3]
            interval = (component.get("interval") or "").lower()
            period = {"1 year": "year", "1 month": "month", "1 hour": "hour"}.get(interval, "year")
            return (
                int(low) if isinstance(low, (int, float)) else None,
                int(high) if isinstance(high, (int, float)) else None,
                currency,
                period,
            )
    return None, None, "", ""
