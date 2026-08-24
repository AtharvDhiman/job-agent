"""End-to-end API behaviour, including every place the agent must stop."""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


def test_health_and_root(client):
    health = client.get("/health").json()
    assert health["status"] in ("ok", "degraded")
    assert health["llm"] == "deterministic"  # no API key configured in tests
    assert client.get("/").json()["compliance"]


def test_register_first_user_becomes_owner(client):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "first@example.com",
            "password": "Str0ng-Password!23",
            "full_name": "First User",
        },
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["role"] == "owner"


def test_weak_password_is_rejected(client):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "weak@example.com", "password": "alllowercaseletters"},
    )
    assert response.status_code == 422
    assert "three of" in response.text


def test_login_does_not_disclose_which_accounts_exist(client, user):
    missing = client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "whatever12345"}
    )
    wrong = client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": "wrong-password-123"}
    )
    assert missing.status_code == wrong.status_code == 401
    assert missing.json()["detail"] == wrong.json()["detail"]


def test_endpoints_require_authentication(client):
    for path in ("/api/v1/profile", "/api/v1/jobs", "/api/v1/dashboard", "/api/v1/audit"):
        assert client.get(path).status_code == 401, path


def test_connector_catalogue_states_every_policy(client):
    connectors = {c["key"]: c for c in client.get("/api/v1/connectors").json()}
    assert connectors["greenhouse"]["compliance_tier"] == "public_job_api"
    assert connectors["greenhouse"]["requires_user_review_by_default"] is True
    for key in ("linkedin", "indeed"):
        assert connectors[key]["automation_permitted_for_submission"] is False
        assert connectors[key]["policy_note"]
    assert all(c["policy_note"] for c in connectors.values())


def test_profile_round_trip(client, auth_headers, profile):
    response = client.put(
        "/api/v1/profile",
        headers=auth_headers,
        json={
            "headline": "Backend engineer",
            "skills": ["python", "go"],
            "min_salary_amount": 150000,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["headline"] == "Backend engineer"
    assert body["min_salary_amount"] == 150000


def test_profile_rejects_a_non_url_link(client, auth_headers, profile):
    response = client.put(
        "/api/v1/profile", headers=auth_headers, json={"portfolio_urls": ["not-a-url"]}
    )
    assert response.status_code == 422


def test_resume_upload_proposes_only_unverified_facts(client, auth_headers, profile):
    resume = b"""Jane Doe
jane@example.com | https://github.com/janedoe

EXPERIENCE
Senior Backend Engineer at Acme Corp, Jan 2021 - Present
- Built a Python and PostgreSQL service

EDUCATION
B.S. Computer Science, State University, 2014 - 2018

SKILLS
Python, PostgreSQL, Docker
"""
    response = client.post(
        "/api/v1/documents",
        headers=auth_headers,
        files={"file": ("resume.txt", resume, "text/plain")},
        data={"kind": "resume_source", "is_primary": "true"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["proposed_fact_count"] > 0
    assert any("UNVERIFIED" in w for w in body["warnings"])

    facts = client.get("/api/v1/facts", headers=auth_headers).json()
    assert facts and all(f["verified"] is False for f in facts)


def test_verifying_facts_is_an_explicit_human_action(client, auth_headers, profile, facts):
    unverified = [
        f for f in client.get("/api/v1/facts", headers=auth_headers).json() if not f["verified"]
    ]
    assert unverified, "fixture should include an unverified fact"
    response = client.post(
        "/api/v1/facts/verify",
        headers=auth_headers,
        json={"fact_ids": [unverified[0]["id"]], "verified": True},
    )
    assert response.status_code == 200
    assert response.json()[0]["verified"] is True


def test_editing_a_fact_revokes_its_verification(client, auth_headers, profile, facts):
    verified = next(
        f for f in client.get("/api/v1/facts", headers=auth_headers).json() if f["verified"]
    )
    response = client.patch(
        f"/api/v1/facts/{verified['id']}",
        headers=auth_headers,
        json={"category": verified["category"], "value": "Edited claim"},
    )
    assert response.status_code == 200
    assert response.json()["verified"] is False


# ------------------------------------------------------- discovery and matching
def test_manual_job_and_rescore(client, auth_headers, profile, facts):
    response = client.post(
        "/api/v1/jobs/manual",
        headers=auth_headers,
        json={
            "title": "Senior Backend Engineer",
            "company": "Northwind Systems",
            "source_url": "https://example.com/jobs/1",
            "description_text": (
                "Python, PostgreSQL, Kubernetes and AWS. $160,000 - $200,000 per year."
            ),
            "location_raw": "Remote - US",
            "work_arrangement": "remote",
            "seniority": "senior",
        },
    )
    assert response.status_code == 201, response.text

    scored = client.post("/api/v1/matches/rescore", headers=auth_headers).json()
    assert scored["scored"] >= 1

    listing = client.get("/api/v1/jobs?min_score=1", headers=auth_headers).json()
    assert listing["total"] >= 1
    match = listing["items"][0]["match"]
    assert match["explanation"]
    assert set(match["component_scores"]) >= {"skills", "semantic", "title", "seniority"}


def test_shortlist_is_ranked(client, auth_headers, profile, facts, db):
    from tests.conftest import make_job

    db.add_all([make_job(), make_job(title="Junior Backend Engineer", seniority="junior")])
    db.flush()
    client.post("/api/v1/matches/rescore", headers=auth_headers)
    shortlist = client.get("/api/v1/jobs/shortlist", headers=auth_headers).json()
    scores = [item["match"]["score"] for item in shortlist]
    assert scores == sorted(scores, reverse=True)


def test_adding_a_manual_only_source_is_refused(client, auth_headers):
    response = client.post(
        "/api/v1/sources",
        headers=auth_headers,
        json={"connector_key": "manual", "identifier": "https://example.com"},
    )
    assert response.status_code == 400
    assert "does not support automated discovery" in response.text


def test_adding_a_partner_source_without_credentials_is_refused(client, auth_headers):
    response = client.post(
        "/api/v1/sources",
        headers=auth_headers,
        json={"connector_key": "linkedin", "identifier": "python"},
    )
    assert response.status_code == 400
    assert "LINKEDIN_PARTNER_API_TOKEN" in response.text


def test_greenhouse_source_is_accepted(client, auth_headers):
    response = client.post(
        "/api/v1/sources",
        headers=auth_headers,
        json={
            "connector_key": "greenhouse",
            "identifier": "examplecorp",
            "display_name": "Example Corp",
        },
    )
    assert response.status_code == 201, response.text
    assert client.get("/api/v1/sources", headers=auth_headers).json()[0]["enabled"] is True


def _greenhouse_source(client, auth_headers) -> dict:
    return client.post(
        "/api/v1/sources",
        headers=auth_headers,
        json={"connector_key": "greenhouse", "identifier": "examplecorp"},
    ).json()


@pytest.mark.parametrize(
    "connector_key,expected",
    [
        ("manual", "does not support automated discovery"),
        ("linkedin", "LINKEDIN_PARTNER_API_TOKEN"),
        ("not_a_connector", "Unknown connector"),
    ],
)
def test_a_source_cannot_be_re_pointed_at_a_platform_it_could_not_be_created_for(
    client, auth_headers, connector_key, expected
):
    """PATCH is not a way around the admission checks POST runs."""
    source = _greenhouse_source(client, auth_headers)
    response = client.patch(
        f"/api/v1/sources/{source['id']}",
        headers=auth_headers,
        json={"connector_key": connector_key, "identifier": "whatever"},
    )
    assert response.status_code == 400, response.text
    assert expected in response.text
    assert (
        client.get("/api/v1/sources", headers=auth_headers).json()[0]["connector_key"]
        == "greenhouse"
    )


def test_a_source_disabled_by_policy_is_not_re_enabled_by_editing_it(client, auth_headers, db):
    """'A BlockedByPolicyError is terminal for that source' -- discovery.py."""
    from app.models.job import JobSourceSubscription

    source = _greenhouse_source(client, auth_headers)
    row = db.get(JobSourceSubscription, uuid.UUID(source["id"]))
    row.enabled = False
    row.last_status = "blocked_by_policy"
    row.last_error = "robots.txt disallows this path"
    db.commit()  # the refused PATCH rolls the request back; this must survive it

    response = client.patch(
        f"/api/v1/sources/{source['id']}",
        headers=auth_headers,
        json={"connector_key": "greenhouse", "identifier": "examplecorp", "enabled": True},
    )
    assert response.status_code == 409, response.text
    assert "not re-enabled by editing" in response.text

    db.refresh(row)
    assert row.enabled is False
