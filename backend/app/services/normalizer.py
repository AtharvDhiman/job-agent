"""RawJob -> Job. Pure functions; no DB, no network. Fully unit-testable."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

from app.connectors.base import BaseConnector, RawJob
from app.core.enums import Seniority, WorkArrangement
from app.models.job import Job
from app.services import taxonomy
from app.services.locations import resolve_city, resolve_country
from app.utils.text import normalize_company, normalize_title, normalize_ws, strip_html

_CURRENCY_SYMBOLS = {"$": "USD", "\u20ac": "EUR", "\u00a3": "GBP", "\u20b9": "INR", "\u00a5": "JPY"}
_SYMBOL_CLASS = r"[$\u20ac\u00a3\u20b9\u00a5]"
_CODE_CLASS = r"USD|EUR|GBP|INR|CAD|AUD|CHF|SEK|SGD|JPY|NZD|ZAR|PLN"
# Amounts are matched permissively, then filtered: a pair only counts as a
# salary if it carries a money signal (symbol, ISO code, thousands grouping,
# k-suffix, or a nearby salary keyword) AND lands inside a plausible band.
# That two-step is what keeps "2019-2024" and "300-500 customers" out.
_AMOUNT = r"(?:\d{1,3}(?:,\d{3})+|\d{1,3}(?:\.\d{3})+|\d{1,3}(?:\.\d)?k|\d{1,7})"
_SALARY_RE = re.compile(
    rf"(?P<pre>{_CODE_CLASS})?\s?(?P<sym>{_SYMBOL_CLASS})?\s?(?P<low>{_AMOUNT})"
    rf"\s?(?:-|to|\u2013|\u2014)\s?"
    rf"(?P<sym2>{_SYMBOL_CLASS})?\s?(?P<high>{_AMOUNT})"
    rf"\s?(?P<code>{_CODE_CLASS})?",
    re.IGNORECASE,
)
_PERIOD_RE = re.compile(
    r"\bper (year|annum|month|hour|day)\b|/\s?(yr|year|hr|hour|mo|month)|\b(annually|hourly)\b",
    re.IGNORECASE,
)
_SALARY_KEYWORD_RE = re.compile(
    r"\b(salary|salaries|compensation|base pay|pay range|pay band|remuneration|ctc|"
    r"package|annual pay|total rewards|rate)\b",
    re.IGNORECASE,
)
_BOUNDS = {
    "year": (8_000, 3_000_000),
    "month": (500, 250_000),
    "week": (200, 60_000),
    "day": (50, 10_000),
    "hour": (5, 2_000),
}


def _parse_amount(token: str) -> int | None:
    token = token.strip().lower().replace(",", "")
    multiplier = 1
    if token.endswith("k"):
        token, multiplier = token[:-1], 1000
    elif token.count(".") == 1 and len(token.split(".")[1]) == 3:
        token = token.replace(".", "")  # European grouping: 55.000
    try:
        return int(float(token) * multiplier)
    except ValueError:
        return None


def _digit_count(token: str) -> int:
    return len([c for c in token if c.isdigit()])


def parse_salary(text: str) -> tuple[int | None, int | None, str, str]:
    """Extract a plausible salary range from prose.

    Conservative by design: when in doubt it returns nothing rather than an
    invented figure, because salary drives a hard filter.
    Returns (min, max, currency, period).
    """
    if not text:
        return None, None, "", ""
    window = text[:8000]
    for match in _SALARY_RE.finditer(window):
        raw_low, raw_high = match.group("low"), match.group("high")
        low, high = _parse_amount(raw_low), _parse_amount(raw_high)
        if low is None or high is None or low > high or low == 0:
            continue

        symbol = match.group("sym") or match.group("sym2") or ""
        code = (match.group("code") or match.group("pre") or "").upper()
        grouped = any("," in v or "." in v or v.lower().endswith("k") for v in (raw_low, raw_high))
        context = window[max(0, match.start() - 120) : match.end() + 120]
        keyword = bool(_SALARY_KEYWORD_RE.search(context))
        if not (symbol or code or grouped or keyword):
            continue

        period = ""
        period_match = _PERIOD_RE.search(context)
        if period_match:
            token = (
                period_match.group(1) or period_match.group(2) or period_match.group(3) or ""
            ).lower()
            period = {
                "year": "year",
                "annum": "year",
                "yr": "year",
                "annually": "year",
                "month": "month",
                "mo": "month",
                "hour": "hour",
                "hr": "hour",
                "hourly": "hour",
                "day": "day",
            }.get(token, "")

        # Small figures are only credible as a rate when money is explicit.
        small = max(_digit_count(raw_low), _digit_count(raw_high)) < 4 and not grouped
        if small and not (symbol or code) and period not in ("hour", "day"):
            continue
        if not period:
            period = "year" if low >= _BOUNDS["year"][0] else "hour"

        floor, ceiling = _BOUNDS.get(period, _BOUNDS["year"])
        if not (floor <= low <= ceiling and floor <= high <= ceiling):
            continue
        return low, high, code or _CURRENCY_SYMBOLS.get(symbol, ""), period
    return None, None, "", ""


_SPONSOR_YES = re.compile(
    r"\b(visa sponsorship (?:is )?(?:available|provided|offered)|we sponsor|"
    r"sponsorship available|will sponsor)\b",
    re.IGNORECASE,
)
_SPONSOR_NO = re.compile(
    r"\b(no visa sponsorship|unable to sponsor|cannot sponsor|not able to sponsor|"
    r"we do not (?:provide|offer) sponsorship|without sponsorship|"
    r"must (?:be )?(?:already )?(?:have|possess) (?:the )?right to work|"
    r"must be authorized to work)\b",
    re.IGNORECASE,
)
# Bullet characters commonly used in job descriptions: hyphen, asterisk,
# bullet (U+2022), black circle (U+25CF), middle dot (U+00B7).
_REQUIREMENT_BULLET = re.compile(
    r"^\s*[-*\u2022\u25cf\u00b7]\s+(.{8,300})$",
    re.MULTILINE,
)


def detect_sponsorship(text: str) -> bool | None:
    """True/False only when the posting says so plainly; None otherwise."""
    if not text:
        return None
    window = text[:12000]
    if _SPONSOR_NO.search(window):
        return False
    if _SPONSOR_YES.search(window):
        return True
    return None


def extract_requirements(text: str, limit: int = 25) -> list[str]:
    bullets = [normalize_ws(m.group(1)) for m in _REQUIREMENT_BULLET.finditer(text or "")]
    seen, out = set(), []
    for bullet in bullets:
        key = bullet.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(bullet)
        if len(out) >= limit:
            break
    return out


def compute_dedupe_hash(company: str, title: str, location_country: str) -> str:
    """Same role on two boards collapses to one hash.

    Location is intentionally coarse (country only) so 'London' and 'London, UK'
    do not split a duplicate, while a genuinely different country stays distinct.
    """
    material = (
        f"{normalize_company(company)}|{normalize_title(title)}|{(location_country or '').upper()}"
    )
    return hashlib.sha256(material.encode()).hexdigest()


def normalize(raw: RawJob, connector: type[BaseConnector], *, now: datetime | None = None) -> Job:
    now = now or datetime.now(UTC)
    description_text = raw.description_text or strip_html(raw.description_html)
    blob = f"{raw.title}\n{raw.location_raw}\n{description_text}"

    country = resolve_country(raw.location_raw) or resolve_country(description_text[:400])
    arrangement = taxonomy.infer_work_arrangement(
        raw.location_raw, description_text, raw.remote_flag
    )
    salary_min, salary_max = raw.salary_min, raw.salary_max
    currency, period = raw.salary_currency, raw.salary_period
    if salary_min is None and salary_max is None:
        salary_min, salary_max, currency, period = parse_salary(description_text)

    seniority = taxonomy.infer_seniority(raw.title, description_text)
    employment = taxonomy.infer_employment_type(raw.employment_type, raw.title)

    return Job(
        connector_key=connector.key,
        compliance_tier=connector.compliance_tier.value,
        submission_policy_default=connector.submission_policy_default.value,
        external_id=raw.external_id[:300],
        source_url=raw.source_url[:1000],
        apply_url=(raw.apply_url or raw.source_url)[:1000],
        is_direct_employer=raw.is_direct_employer and connector.direct_employer,
        title=normalize_ws(raw.title)[:400],
        title_normalized=normalize_title(raw.title)[:400],
        company=normalize_ws(raw.company)[:300],
        company_normalized=normalize_company(raw.company)[:300],
        department=normalize_ws(raw.department)[:200],
        description_text=description_text,
        description_html=raw.description_html,
        location_raw=normalize_ws(raw.location_raw)[:400],
        location_city=resolve_city(raw.location_raw)[:160],
        location_country=country,
        work_arrangement=arrangement.value,
        employment_type=employment.value,
        seniority=seniority.value,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=(currency or "")[:3],
        salary_period=(period or "")[:16],
        posted_at=raw.posted_at,
        deadline_at=raw.deadline_at,
        first_seen_at=now,
        last_seen_at=now,
        extracted_skills=taxonomy.extract_skills(blob),
        requirements=extract_requirements(description_text),
        visa_sponsorship_mentioned=detect_sponsorship(description_text),
        raw=raw.raw,
        dedupe_hash=compute_dedupe_hash(raw.company, raw.title, country),
    )


def is_stale(job: Job, max_age_hours: int, *, now: datetime | None = None) -> bool:
    """A posting with no date is treated as fresh once, then ages from first_seen."""
    now = now or datetime.now(UTC)
    reference = job.posted_at or job.first_seen_at
    if reference is None:
        return False
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    return (now - reference).total_seconds() > max_age_hours * 3600


def unknown_seniority(job: Job) -> bool:
    return job.seniority == Seniority.UNKNOWN.value


def unknown_arrangement(job: Job) -> bool:
    return job.work_arrangement == WorkArrangement.UNKNOWN.value
