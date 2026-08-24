"""SmartRecruiters public Posting API.

Discovery: PUBLIC_JOB_API (api.smartrecruiters.com/v1/companies/<id>/postings).
Submission: REVIEW_REQUIRED. Their write API needs a partner key; if you hold
one, put it in the environment and use it -- we never fabricate credentials.
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

BASE = "https://api.smartrecruiters.com/v1/companies"
PAGE = 100
MAX_PAGES = 10


@registry.register
class SmartRecruitersConnector(BaseConnector):
    key = "smartrecruiters"
    display_name = "SmartRecruiters"
    compliance_tier = ComplianceTier.PUBLIC_JOB_API
    submission_policy_default = SubmissionPolicy.REVIEW_REQUIRED
    policy_note = (
        "Public Posting API. Applications are always queued for your review unless "
        "you authorize the platform; the write API requires a partner agreement."
    )
    identifier_label = "Company identifier"
    identifier_help = "The slug in careers.smartrecruiters.com/<identifier>."

    def fetch(self, spec: SourceSpec, *, etag: str = "") -> FetchResult:
        company = spec.identifier.strip().strip("/")
        if not company:
            raise ConnectorError("SmartRecruiters company identifier is required")

        jobs: list[RawJob] = []
        offset = 0
        for _ in range(MAX_PAGES):
            url = f"{BASE}/{company}/postings?limit={PAGE}&offset={offset}"
            payload, _new_etag = self.http.get_json(url, check_robots=False)
            if not isinstance(payload, dict):
                raise ConnectorError(f"Unexpected SmartRecruiters payload for '{company}'")
            content = payload.get("content") or []
            for item in content:
                jobs.append(self._to_raw(item, company, spec))
            offset += PAGE
            if offset >= int(payload.get("totalFound") or 0) or not content:
                break
        return FetchResult(jobs=jobs, etag="")

    def _to_raw(self, item: dict, company: str, spec: SourceSpec) -> RawJob:
        location = item.get("location") or {}
        parts = [location.get("city"), location.get("region"), location.get("country")]
        location_raw = ", ".join(p for p in parts if p)
        posting_id = item.get("id") or item.get("uuid")
        detail = self._detail(company, posting_id) if spec.config.get("fetch_detail", True) else {}
        html_desc = _sections_to_html(detail)
        employer = (item.get("company") or {}).get("name") or spec.display_name or company
        return RawJob(
            external_id=f"{company}:{posting_id}",
            title=(item.get("name") or "").strip(),
            company=employer,
            source_url=(detail.get("applyUrl") or item.get("ref") or ""),
            apply_url=detail.get("applyUrl") or "",
            description_html=html_desc,
            description_text=strip_html(html_desc),
            location_raw=location_raw,
            department=(item.get("department") or {}).get("label", "") or "",
            employment_type=(item.get("typeOfEmployment") or {}).get("label", "") or "",
            posted_at=parse_datetime(item.get("releasedDate") or item.get("createdOn")),
            remote_flag=bool(location.get("remote")) if "remote" in location else None,
            is_direct_employer=True,
            raw={
                "id": posting_id,
                "company": company,
                "experienceLevel": (item.get("experienceLevel") or {}).get("label"),
            },
        )

    def _detail(self, company: str, posting_id) -> dict:
        """Job ad body lives on the detail endpoint. Failure is non-fatal."""
        if not posting_id:
            return {}
        try:
            payload, _ = self.http.get_json(
                f"{BASE}/{company}/postings/{posting_id}", check_robots=False
            )
        except ConnectorError:
            return {}
        return payload if isinstance(payload, dict) else {}


def _sections_to_html(detail: dict) -> str:
    sections = ((detail.get("jobAd") or {}).get("sections")) or {}
    order = ("companyDescription", "jobDescription", "qualifications", "additionalInformation")
    chunks = []
    for name in order:
        section = sections.get(name) or {}
        title = section.get("title") or name
        text = section.get("text") or ""
        if text:
            chunks.append(f"<h3>{title}</h3>{text}")
    return "".join(chunks)
