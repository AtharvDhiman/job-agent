"""Platforms that require YOUR OWN API agreement, plus the two we never automate.

LinkedIn and Indeed are registered so the UI can explain *why* they are absent
rather than silently omitting them. Both refuse to fetch without partner
credentials and both pin submission to PROHIBITED. See docs/COMPLIANCE.md.
"""

from __future__ import annotations

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
from app.utils.text import parse_datetime, strip_html


@registry.register
class LinkedInConnector(BaseConnector):
    key = "linkedin"
    display_name = "LinkedIn (partner API only)"
    compliance_tier = ComplianceTier.PARTNER_API
    submission_policy_default = SubmissionPolicy.PROHIBITED
    policy_note = (
        "Scraping LinkedIn violates its User Agreement, so this connector only "
        "runs against an official partner API using a token YOU hold. Automated "
        "applying (including Easy Apply) is PROHIBITED here and cannot be enabled: "
        "matched roles become review tasks with a link you open yourself."
    )
    required_credentials = ("LINKEDIN_PARTNER_API_TOKEN",)
    identifier_label = "Partner API query"
    identifier_help = "Only usable with your own LinkedIn Talent Solutions access."

    def fetch(self, spec: SourceSpec, *, etag: str = "") -> FetchResult:
        token = getattr(self.settings, "linkedin_partner_api_token", "")
        if not token:
            raise BlockedByPolicyError(
                "No LinkedIn partner API token configured. This connector will not scrape "
                "linkedin.com as a substitute. Search LinkedIn yourself and paste a job URL, "
                "or add the employer's ATS board (Greenhouse/Lever/Ashby) instead."
            )
        raise ConnectorError(
            "A LinkedIn partner token is present but no partner endpoint is configured. "
            "Set the endpoint in this connector to match the contract your agreement grants; "
            "the shape of that API differs per partner tier."
        )


@registry.register
class IndeedConnector(BaseConnector):
    key = "indeed"
    display_name = "Indeed (partner API only)"
    compliance_tier = ComplianceTier.PARTNER_API
    submission_policy_default = SubmissionPolicy.PROHIBITED
    policy_note = (
        "Indeed's public site forbids automated access; API access is by agreement. "
        "This connector only runs with your own publisher/employer token, and "
        "automated applying is PROHIBITED and cannot be enabled."
    )
    required_credentials = ("INDEED_PUBLISHER_API_TOKEN",)
    identifier_label = "Publisher API query"
    identifier_help = "Only usable with your own Indeed API agreement."

    def fetch(self, spec: SourceSpec, *, etag: str = "") -> FetchResult:
        token = getattr(self.settings, "indeed_publisher_api_token", "")
        if not token:
            raise BlockedByPolicyError(
                "No Indeed API token configured. This connector will not scrape indeed.com "
                "as a substitute. Add the employer's ATS board instead, or paste a job URL."
            )
        raise ConnectorError(
            "An Indeed token is present but no endpoint is configured. Point this connector "
            "at the exact endpoint your agreement grants."
        )


@registry.register
class AdzunaConnector(BaseConnector):
    key = "adzuna"
    display_name = "Adzuna"
    compliance_tier = ComplianceTier.PARTNER_API
    submission_policy_default = SubmissionPolicy.REVIEW_REQUIRED
    policy_note = (
        "Adzuna publishes a free developer API; you register for your own app id "
        "and key. Results link out to the employer, so applications go to review."
    )
    required_credentials = ("ADZUNA_APP_ID", "ADZUNA_APP_KEY")
    direct_employer = False
    identifier_label = "Country code and query"
    identifier_help = "Format: '<country>:<what>', e.g. 'gb:python engineer'."

    def fetch(self, spec: SourceSpec, *, etag: str = "") -> FetchResult:
        app_id = getattr(self.settings, "adzuna_app_id", "")
        app_key = getattr(self.settings, "adzuna_app_key", "")
        if not (app_id and app_key):
            raise BlockedByPolicyError("Adzuna needs ADZUNA_APP_ID and ADZUNA_APP_KEY.")
        country, _, what = spec.identifier.partition(":")
        country = (country or "gb").strip().lower()
        params = {
            "app_id": app_id,
            "app_key": app_key,
            "results_per_page": str(spec.config.get("results_per_page", 50)),
            "what": what.strip(),
            "max_days_old": str(spec.config.get("max_days_old", 3)),
            "content-type": "application/json",
        }
        if spec.config.get("where"):
            params["where"] = spec.config["where"]
        url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
        payload, _ = self.http.get_json(url, params=params, check_robots=False)
        if not isinstance(payload, dict):
            raise ConnectorError("Unexpected Adzuna payload")

        jobs = []
        for item in payload.get("results") or []:
            body = item.get("description") or ""
            company = (item.get("company") or {}).get("display_name") or ""
            jobs.append(
                RawJob(
                    external_id=f"adzuna:{item.get('id')}",
                    title=(item.get("title") or "").strip(),
                    company=company,
                    source_url=item.get("redirect_url", ""),
                    apply_url=item.get("redirect_url", ""),
                    description_html=body,
                    description_text=strip_html(body),
                    location_raw=(item.get("location") or {}).get("display_name", ""),
                    employment_type="full_time" if item.get("contract_time") == "full_time" else "",
                    posted_at=parse_datetime(item.get("created")),
                    salary_min=int(item["salary_min"]) if item.get("salary_min") else None,
                    salary_max=int(item["salary_max"]) if item.get("salary_max") else None,
                    salary_currency=spec.config.get("currency", ""),
                    salary_period="year",
                    is_direct_employer=False,
                    raw={"adzuna_id": item.get("id"), "country": country},
                )
            )
        return FetchResult(jobs=jobs, etag="")


@registry.register
class ManualConnector(BaseConnector):
    key = "manual"
    display_name = "Manually added job"
    compliance_tier = ComplianceTier.MANUAL_ONLY
    submission_policy_default = SubmissionPolicy.REVIEW_REQUIRED
    policy_note = (
        "You paste a URL and the details. Nothing is fetched automatically. "
        "Use this for any site that blocks automation."
    )
    identifier_label = "Job URL"
    identifier_help = "Paste the posting URL; fill in the details yourself."

    def fetch(self, spec: SourceSpec, *, etag: str = "") -> FetchResult:
        raise BlockedByPolicyError(
            "The manual connector never fetches. Create the job through "
            "POST /api/v1/jobs/manual instead."
        )
