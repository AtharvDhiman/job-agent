"""Connector parsing against recorded response shapes (no live network)."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.connectors import PoliteClient, SourceSpec, registry
from app.connectors.base import BlockedByPolicyError, ConnectorError
from app.core.config import settings


@pytest.fixture
def http():
    with PoliteClient() as client:
        yield client


def spec(connector_key: str, identifier: str, **config) -> SourceSpec:
    return SourceSpec(connector_key=connector_key, identifier=identifier, config=config)


@respx.mock
def test_greenhouse_parses_a_board(http):
    respx.get("https://boards-api.greenhouse.io/v1/boards/example/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": 4321,
                        "title": "Senior Backend Engineer",
                        "absolute_url": "https://boards.greenhouse.io/example/jobs/4321",
                        "location": {"name": "Remote - US"},
                        "departments": [{"name": "Engineering"}],
                        "content": "&lt;p&gt;Python and PostgreSQL.&lt;/p&gt;",
                        "first_published": "2026-08-24T08:00:00Z",
                    }
                ]
            },
        )
    )
    result = registry.get("greenhouse")(http, settings=settings).fetch(
        spec("greenhouse", "example")
    )
    assert len(result.jobs) == 1
    job = result.jobs[0]
    assert job.external_id == "example:4321"
    assert job.title == "Senior Backend Engineer"
    assert job.location_raw == "Remote - US"
    assert job.posted_at is not None
    assert job.is_direct_employer is True


@respx.mock
def test_lever_parses_postings(http):
    respx.get("https://api.lever.co/v0/postings/example").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "abc-123",
                    "text": "Data Engineer",
                    "categories": {
                        "location": "Berlin, Germany",
                        "department": "Data",
                        "commitment": "Full-time",
                    },
                    "descriptionPlain": "Build pipelines with Python and Spark.",
                    "description": "<p>Build pipelines.</p>",
                    "lists": [{"text": "Requirements", "content": "<li>3 years Python</li>"}],
                    "hostedUrl": "https://jobs.lever.co/example/abc-123",
                    "applyUrl": "https://jobs.lever.co/example/abc-123/apply",
                    "createdAt": 1756000000000,
                    "workplaceType": "remote",
                }
            ],
        )
    )
    result = registry.get("lever")(http, settings=settings).fetch(spec("lever", "example"))
    job = result.jobs[0]
    assert job.external_id == "example:abc-123"
    assert job.remote_flag is True
    assert job.employment_type == "Full-time"
    assert job.posted_at.year >= 2025


@respx.mock
def test_ashby_parses_compensation_and_skips_unlisted(http):
    respx.get("https://api.ashbyhq.com/posting-api/job-board/example").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "Example Corp",
                "jobs": [
                    {
                        "id": "job-1",
                        "title": "Platform Engineer",
                        "location": "Remote",
                        "isListed": True,
                        "isRemote": True,
                        "descriptionPlain": "Kubernetes and Terraform.",
                        "publishedAt": "2026-08-23T10:00:00Z",
                        "jobUrl": "https://jobs.ashbyhq.com/example/job-1",
                        "compensation": {
                            "compensationTiers": [
                                {
                                    "components": [
                                        {
                                            "compensationType": "Salary",
                                            "minValue": 140000,
                                            "maxValue": 180000,
                                            "currencyCode": "usd",
                                            "interval": "1 YEAR",
                                        }
                                    ]
                                }
                            ]
                        },
                    },
                    {"id": "job-2", "title": "Hidden", "isListed": False},
                ],
            },
        )
    )
    result = registry.get("ashby")(http, settings=settings).fetch(spec("ashby", "example"))
    assert len(result.jobs) == 1
    job = result.jobs[0]
    assert job.company == "Example Corp"
    assert (job.salary_min, job.salary_max, job.salary_currency) == (140000, 180000, "USD")


@respx.mock
def test_workable_parses_and_filters_unpublished(http):
    respx.get("https://apply.workable.com/api/v1/widget/accounts/example").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "Example GmbH",
                "jobs": [
                    {
                        "id": 1,
                        "shortcode": "ABC123",
                        "title": "QA Engineer",
                        "state": "published",
                        "url": "https://apply.workable.com/example/j/ABC123/",
                        "application_url": "https://apply.workable.com/example/j/ABC123/apply/",
                        "location": {
                            "city": "Munich",
                            "country": "Germany",
                            "telecommuting": False,
                        },
                        "description": "<p>Testing with Playwright.</p>",
                        "requirements": "<li>Playwright</li>",
                        "created_at": "2026-08-22",
                        "employment_type": "Full-time",
                    },
                    {"id": 2, "shortcode": "XYZ", "title": "Draft", "state": "draft"},
                ],
            },
        )
    )
    result = registry.get("workable")(http, settings=settings).fetch(spec("workable", "example"))
    assert len(result.jobs) == 1
    assert result.jobs[0].location_raw == "Munich, Germany"
    assert "Playwright" in result.jobs[0].description_text


@respx.mock
def test_greenhouse_honours_a_304(http):
    respx.get("https://boards-api.greenhouse.io/v1/boards/example/jobs").mock(
        return_value=httpx.Response(304)
    )
    result = registry.get("greenhouse")(http, settings=settings).fetch(
        spec("greenhouse", "example"), etag='W/"abc"'
    )
    assert result.jobs == []
    assert "not modified" in result.notes


@respx.mock
def test_a_403_is_treated_as_a_policy_block_not_a_retry(http):
    respx.get("https://boards-api.greenhouse.io/v1/boards/example/jobs").mock(
        return_value=httpx.Response(403)
    )
    with pytest.raises(BlockedByPolicyError, match="gated"):
        registry.get("greenhouse")(http, settings=settings).fetch(spec("greenhouse", "example"))


@respx.mock
def test_a_429_is_a_retryable_connector_error(http):
    respx.get("https://boards-api.greenhouse.io/v1/boards/example/jobs").mock(
        return_value=httpx.Response(429)
    )
    with pytest.raises(ConnectorError, match="rate-limited"):
        registry.get("greenhouse")(http, settings=settings).fetch(spec("greenhouse", "example"))


@respx.mock
def test_careers_page_reads_only_structured_data(http):
    respx.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nAllow: /")
    )
    respx.get("https://example.com/careers").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="""
            <html><head><script type="application/ld+json">
            {"@context":"https://schema.org","@type":"JobPosting",
             "title":"Site Reliability Engineer",
             "description":"<p>Run the platform.</p>",
             "datePosted":"2026-08-23",
             "hiringOrganization":{"@type":"Organization","name":"Example Ltd"},
             "jobLocationType":"TELECOMMUTE",
             "applicantLocationRequirements":{"@type":"Country","name":"Germany"},
             "baseSalary":{"@type":"MonetaryAmount","currency":"EUR",
                "value":{"@type":"QuantitativeValue","minValue":70000,
                         "maxValue":90000,"unitText":"YEAR"}}}
            </script></head><body><h1>Careers</h1></body></html>
            """,
        )
    )
    result = registry.get("careers_page")(http, settings=settings).fetch(
        spec("careers_page", "https://example.com/careers")
    )
    job = result.jobs[0]
    assert job.title == "Site Reliability Engineer"
    assert job.company == "Example Ltd"
    assert job.location_raw == "Remote (Germany)"
    assert (job.salary_min, job.salary_max, job.salary_currency) == (70000, 90000, "EUR")


@respx.mock
def test_careers_page_refuses_to_scrape_a_page_without_structured_data(http):
    respx.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nAllow: /")
    )
    respx.get("https://example.com/careers").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><body><div class='job'>Engineer</div></body></html>",
        )
    )
    with pytest.raises(BlockedByPolicyError, match="will not scrape"):
        registry.get("careers_page")(http, settings=settings).fetch(
            spec("careers_page", "https://example.com/careers")
        )


@respx.mock
def test_robots_disallow_stops_the_fetch(http):
    respx.get("https://blocked.example/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /careers")
    )
    with pytest.raises(BlockedByPolicyError, match="robots.txt disallows"):
        registry.get("careers_page")(http, settings=settings).fetch(
            spec("careers_page", "https://blocked.example/careers")
        )


@respx.mock
def test_careers_page_aborts_on_a_bot_check(http):
    respx.get("https://guarded.example/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://guarded.example/jobs").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><body><div class='g-recaptcha'></div></body></html>",
        )
    )
    with pytest.raises(BlockedByPolicyError, match="bot-check"):
        registry.get("careers_page")(http, settings=settings).fetch(
            spec("careers_page", "https://guarded.example/jobs")
        )


@respx.mock
def test_rss_feed_parsing(http):
    respx.get("https://jobs.example.org/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://jobs.example.org/feed.xml").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/rss+xml"},
            text="""<?xml version="1.0"?><rss version="2.0"><channel>
            <item><title>Backend Engineer</title>
            <link>https://jobs.example.org/1</link>
            <guid>https://jobs.example.org/1</guid>
            <description>Python and Go</description>
            <pubDate>Sat, 23 Aug 2026 12:00:00 +0000</pubDate></item>
            </channel></rss>""",
        )
    )
    result = registry.get("rss")(http, settings=settings).fetch(
        spec("rss", "https://jobs.example.org/feed.xml")
    )
    assert result.jobs[0].title == "Backend Engineer"
    assert result.jobs[0].posted_at is not None


def test_linkedin_refuses_to_scrape_without_partner_credentials(http):
    with pytest.raises(BlockedByPolicyError, match="will not scrape"):
        registry.get("linkedin")(http, settings=settings).fetch(spec("linkedin", "python jobs"))


def test_indeed_refuses_to_scrape_without_partner_credentials(http):
    with pytest.raises(BlockedByPolicyError, match="will not scrape"):
        registry.get("indeed")(http, settings=settings).fetch(spec("indeed", "python jobs"))
