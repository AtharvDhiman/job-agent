from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.connectors.base import RawJob
from app.connectors.greenhouse import GreenhouseConnector
from app.core.enums import Seniority, WorkArrangement
from app.services.normalizer import (
    compute_dedupe_hash,
    detect_sponsorship,
    extract_requirements,
    is_stale,
    normalize,
    parse_salary,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Range: $120,000 - $160,000 per year", (120000, 160000, "USD", "year")),
        ("EUR 55.000 - 75.000 per year", (55000, 75000, "EUR", "year")),
        ("Compensation: 120k-150k", (120000, 150000, "", "year")),
        ("Rate: $45 - $65 per hour", (45, 65, "USD", "hour")),
        ("Founded 2019-2024 with 300-500 customers", (None, None, "", "")),
        ("We shipped 1000-2000 units", (None, None, "", "")),
        ("Team of 5-10 engineers", (None, None, "", "")),
        ("", (None, None, "", "")),
    ],
)
def test_parse_salary(text, expected):
    assert parse_salary(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Visa sponsorship is available.", True),
        ("We are unable to sponsor visas.", False),
        ("You must be authorized to work in the US.", False),
        ("Great team, great benefits.", None),
    ],
)
def test_detect_sponsorship(text, expected):
    assert detect_sponsorship(text) is expected


def test_dedupe_collapses_cosmetic_variants():
    assert compute_dedupe_hash("Acme, Inc.", "Sr. Engineer", "US") == compute_dedupe_hash(
        "ACME Inc", "Senior Engineer (Remote)", "US"
    )


def test_dedupe_keeps_genuinely_different_roles_apart():
    base = compute_dedupe_hash("Acme", "Engineer II", "US")
    assert base != compute_dedupe_hash("Acme", "Engineer III", "US")
    assert base != compute_dedupe_hash("Acme", "Engineer II", "GB")
    assert base != compute_dedupe_hash("Globex", "Engineer II", "US")


def test_extract_requirements_dedupes_and_limits():
    text = "- Five years of Python\n- Five years of Python\n- Strong SQL\n"
    assert extract_requirements(text) == ["Five years of Python", "Strong SQL"]


def test_normalize_produces_a_complete_job():
    raw = RawJob(
        external_id="example:1",
        title="Senior Python Engineer (Remote)",
        company="Northwind Systems, Inc.",
        source_url="https://boards.greenhouse.io/example/jobs/1",
        description_html=(
            "<p>We need a Senior Python Engineer.</p><ul><li>5+ years with Django</li></ul>"
            "<p>The salary range is $150,000 - $190,000 per year. "
            "Visa sponsorship is available.</p>"
        ),
        location_raw="Remote - Austin, TX",
        posted_at=datetime.now(UTC) - timedelta(hours=3),
    )
    job = normalize(raw, GreenhouseConnector)

    assert job.connector_key == "greenhouse"
    assert job.compliance_tier == "public_job_api"
    assert job.submission_policy_default == "review_required"
    assert job.company_normalized == "northwind systems"
    assert job.location_country == "US"
    assert job.work_arrangement == WorkArrangement.REMOTE.value
    assert job.seniority == Seniority.SENIOR.value
    assert job.salary_min == 150000 and job.salary_currency == "USD"
    assert job.visa_sponsorship_mentioned is True
    assert "python" in job.extracted_skills and "django" in job.extracted_skills
    assert job.requirements == ["5+ years with Django"]
    assert "<p>" not in job.description_text


def test_hybrid_beats_remote_when_both_words_appear():
    raw = RawJob(
        external_id="x",
        title="Analyst",
        company="Contoso",
        source_url="https://example.com",
        description_text="Remote friendly team, hybrid role with 3 days per week in office",
        location_raw="Austin, TX",
    )
    assert normalize(raw, GreenhouseConnector).work_arrangement == WorkArrangement.HYBRID.value


def test_is_stale_uses_posted_at_then_first_seen():
    now = datetime.now(UTC)
    raw = RawJob(external_id="x", title="A", company="B", source_url="https://e.com")
    job = normalize(raw, GreenhouseConnector, now=now - timedelta(hours=72))
    assert is_stale(job, 48, now=now) is True
    assert is_stale(job, 96, now=now) is False
