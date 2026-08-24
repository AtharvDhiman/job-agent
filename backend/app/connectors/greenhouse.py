"""Greenhouse Job Board API.

Discovery: PUBLIC_JOB_API. Greenhouse publishes an unauthenticated Job Board
API so companies can render their own board; reading it is its intended use.
Submission: REVIEW_REQUIRED by default. Greenhouse-hosted application forms can
be filled by the local assistant once YOU authorize the platform, because they
are plain forms with no login. Any CAPTCHA on the page still aborts.
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

BASE = "https://boards-api.greenhouse.io/v1/boards"


@registry.register
class GreenhouseConnector(BaseConnector):
    key = "greenhouse"
    display_name = "Greenhouse"
    compliance_tier = ComplianceTier.PUBLIC_JOB_API
    submission_policy_default = SubmissionPolicy.REVIEW_REQUIRED
    policy_note = (
        "Public, documented job-board API intended for public consumption. "
        "Application submission stays in review until you explicitly authorize "
        "the platform; even then a CAPTCHA or login aborts the run."
    )
    identifier_label = "Board token"
    identifier_help = "The slug in boards.greenhouse.io/<token>, e.g. 'examplecorp'."

    def fetch(self, spec: SourceSpec, *, etag: str = "") -> FetchResult:
        token = spec.identifier.strip().strip("/")
        if not token:
            raise ConnectorError("Greenhouse board token is required")
        url = f"{BASE}/{token}/jobs?content=true"
        payload, new_etag = self.http.get_json(url, etag=etag, check_robots=False)
        if payload is None:  # 304 Not Modified
            return FetchResult(jobs=[], etag=etag, notes=["not modified"])
        if not isinstance(payload, dict) or "jobs" not in payload:
            raise ConnectorError(f"Unexpected Greenhouse payload for board '{token}'")

        jobs: list[RawJob] = []
        for item in payload.get("jobs") or []:
            try:
                jobs.append(self._to_raw(item, token, spec))
            except (KeyError, TypeError, ValueError) as exc:
                raise ConnectorError(f"Malformed Greenhouse job in '{token}': {exc}") from exc
        return FetchResult(jobs=jobs, etag=new_etag or etag)

    def _to_raw(self, item: dict, token: str, spec: SourceSpec) -> RawJob:
        content_html = item.get("content") or ""
        offices = item.get("offices") or []
        departments = item.get("departments") or []
        location = (item.get("location") or {}).get("name") or ""
        if not location and offices:
            location = ", ".join(o.get("name", "") for o in offices if o.get("name"))
        company = spec.display_name or spec.config.get("company") or token
        posted = parse_datetime(item.get("first_published") or item.get("updated_at"))
        return RawJob(
            external_id=f"{token}:{item['id']}",
            title=item.get("title", "").strip(),
            company=company,
            source_url=item.get("absolute_url", ""),
            apply_url=item.get("absolute_url", ""),
            description_html=content_html,
            description_text=strip_html(content_html),
            location_raw=location,
            department=", ".join(d.get("name", "") for d in departments if d.get("name")),
            posted_at=posted,
            is_direct_employer=True,
            raw={
                "id": item.get("id"),
                "internal_job_id": item.get("internal_job_id"),
                "requisition_id": item.get("requisition_id"),
                "metadata": item.get("metadata"),
                "board_token": token,
            },
        )
