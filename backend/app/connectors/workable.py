"""Workable public account widget API.

Discovery: PUBLIC_JOB_API (apply.workable.com/api/v1/widget/accounts/<sub>).
Submission: REVIEW_REQUIRED.
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

BASE = "https://apply.workable.com/api/v1/widget/accounts"


@registry.register
class WorkableConnector(BaseConnector):
    key = "workable"
    display_name = "Workable"
    compliance_tier = ComplianceTier.PUBLIC_JOB_API
    submission_policy_default = SubmissionPolicy.REVIEW_REQUIRED
    policy_note = (
        "Public account widget API used to embed a company's board. "
        "Applications are queued for review unless you authorize the platform."
    )
    identifier_label = "Workable subdomain"
    identifier_help = "The slug in apply.workable.com/<subdomain>."

    def fetch(self, spec: SourceSpec, *, etag: str = "") -> FetchResult:
        sub = spec.identifier.strip().strip("/")
        if not sub:
            raise ConnectorError("Workable subdomain is required")
        url = f"{BASE}/{sub}?details=true"
        payload, new_etag = self.http.get_json(url, etag=etag, check_robots=False)
        if payload is None:
            return FetchResult(jobs=[], etag=etag, notes=["not modified"])
        if not isinstance(payload, dict):
            raise ConnectorError(f"Unexpected Workable payload for '{sub}'")

        company = payload.get("name") or spec.display_name or sub
        jobs = []
        for item in payload.get("jobs") or []:
            if (item.get("state") or "published").lower() not in ("published", ""):
                continue
            location = item.get("location") or {}
            loc_parts = [location.get("city"), location.get("region"), location.get("country")]
            html_desc = "".join(
                filter(
                    None,
                    [
                        item.get("description") or "",
                        f"<h3>Requirements</h3>{item.get('requirements')}"
                        if item.get("requirements")
                        else "",
                        f"<h3>Benefits</h3>{item.get('benefits')}" if item.get("benefits") else "",
                    ],
                )
            )
            jobs.append(
                RawJob(
                    external_id=f"{sub}:{item.get('shortcode') or item.get('id')}",
                    title=(item.get("title") or "").strip(),
                    company=company,
                    source_url=item.get("url") or item.get("shortlink") or "",
                    apply_url=item.get("application_url") or item.get("url") or "",
                    description_html=html_desc,
                    description_text=strip_html(html_desc),
                    location_raw=", ".join(p for p in loc_parts if p),
                    department=item.get("department") or "",
                    employment_type=item.get("employment_type") or "",
                    posted_at=parse_datetime(item.get("created_at") or item.get("published_on")),
                    remote_flag=location.get("telecommuting"),
                    is_direct_employer=True,
                    raw={"shortcode": item.get("shortcode"), "account": sub},
                )
            )
        return FetchResult(jobs=jobs, etag=new_etag or etag)
