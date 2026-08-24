"""Autopilot: one call runs the pipeline, the human only flips the gates.

The pipeline must never do a human's job: facts stay unverified, drafts without
an authorization become review tasks, and the profile fields a human typed are
never overwritten by a parser guess.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.application import Application
from app.schemas.settings import AUTHORIZATION_ACKNOWLEDGEMENT

pytestmark = pytest.mark.integration


@pytest.fixture
def automation_on(monkeypatch):
    monkeypatch.setattr(settings, "automation_global_enabled", True)
    yield
    monkeypatch.setattr(settings, "automation_global_enabled", False)


def authorize(client, auth_headers, policy="auto_submit", platform="greenhouse"):
    return client.post(
        "/api/v1/settings/authorizations",
        headers=auth_headers,
        json={
            "platform_key": platform,
            "policy": policy,
            "acknowledgement": AUTHORIZATION_ACKNOWLEDGEMENT,
        },
    )


RESUME = b"""Jane Doe
jane@example.com | https://www.linkedin.com/in/janedoe | https://github.com/janedoe

EXPERIENCE
Senior Backend Engineer at Acme Corp, Jan 2021 - Present
- Built a Python and PostgreSQL service with 7+ years of experience behind it

Backend Engineer at Beta LLC, Jun 2018 - Dec 2020
- Shipped Docker-based deployments

EDUCATION
B.S. Computer Science, State University, 2014 - 2018

SKILLS
Python, PostgreSQL, Docker
"""


def upload_resume(client, auth_headers, content=RESUME, name="resume.txt"):
    return client.post(
        "/api/v1/documents",
        headers=auth_headers,
        files={"file": (name, content, "text/plain")},
        data={"kind": "resume_source", "is_primary": "true"},
    )


GATE_FIELDS = {
    "automation_enabled",
    "global_automation_enabled",
    "authorized_platforms",
    "verified_fact_count",
    "unverified_fact_count",
    "enabled_source_count",
    "resume_uploaded",
    "applications_today",
    "daily_application_limit",
    "queued_application_count",
}


# ------------------------------------------------------------------- status
def test_status_reports_every_gate_and_the_missing_steps(client, auth_headers, user):
    body = client.get("/api/v1/autopilot/status", headers=auth_headers).json()
    assert GATE_FIELDS <= set(body)
    assert body["resume_uploaded"] is False
    assert body["automation_enabled"] is False
    assert body["global_automation_enabled"] is False
    assert body["authorized_platforms"] == []
    assert body["queued_application_count"] == 0

    steps = body["next_steps"]
    assert "Upload a resume" in steps
    assert "Enable at least one job source under Settings" in steps
    assert "Resume automation (it is paused)" in steps
    assert any("AUTOMATION_GLOBAL_ENABLED" in s for s in steps)
    assert any("Authorize at least one platform" in s for s in steps)
    # Nothing queued yet, so the assistant step would only be noise.
    assert not any("browser assistant" in s for s in steps)


# --------------------------------------------------------------------- run
def test_run_without_authorization_sends_every_draft_to_review(
    client, auth_headers, profile, facts, job
):
    response = client.post("/api/v1/autopilot/run", headers=auth_headers, json={})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["scoring"]["scored"] >= 1
    assert body["scoring"]["shortlisted"] >= 1
    assert body["drafting"]["drafted"] >= 1
    # No platform authorization and the global switch is off: the policy gate
    # must route every single draft to a human, none to the submit queue.
    assert body["drafting"]["queued_for_auto_submit"] == 0
    assert body["drafting"]["sent_to_review"] == body["drafting"]["drafted"]
    assert GATE_FIELDS <= set(body["gates"])
    assert isinstance(body["next_steps"], list)


def test_run_with_authorization_queues_for_auto_submit(
    client, auth_headers, profile, facts, job, automation_on
):
    authorize(client, auth_headers)
    client.post("/api/v1/settings/resume", headers=auth_headers)

    body = client.post("/api/v1/autopilot/run", headers=auth_headers, json={}).json()
    assert body["drafting"]["drafted"] >= 1
    assert body["drafting"]["queued_for_auto_submit"] >= 1
    assert body["gates"]["queued_application_count"] >= 1
    assert any("browser assistant" in s for s in body["next_steps"])


def test_run_twice_does_not_duplicate_applications(client, auth_headers, profile, facts, job, db):
    first = client.post("/api/v1/autopilot/run", headers=auth_headers, json={}).json()
    assert first["drafting"]["drafted"] >= 1
    count_after_first = len(db.execute(select(Application)).scalars().all())

    second = client.post("/api/v1/autopilot/run", headers=auth_headers, json={}).json()
    assert second["drafting"]["drafted"] == 0
    assert len(db.execute(select(Application)).scalars().all()) == count_after_first


def test_run_can_skip_drafting(client, auth_headers, profile, facts, job):
    body = client.post(
        "/api/v1/autopilot/run", headers=auth_headers, json={"include_drafting": False}
    ).json()
    assert body["drafting"] == {"drafted": 0, "queued_for_auto_submit": 0, "sent_to_review": 0}
    assert body["scoring"]["scored"] >= 1


# ----------------------------------------------------- upload auto-configure
def test_upload_fills_only_empty_profile_fields(client, auth_headers, profile, db):
    profile.skills = []
    profile.target_titles = []
    profile.linkedin_url = ""
    profile.portfolio_urls = []
    profile.seniority_level = "unknown"
    db.flush()

    response = upload_resume(client, auth_headers)
    assert response.status_code == 201, response.text
    body = response.json()

    configured = body["auto_configured"]
    assert set(configured) >= {"skills", "target_titles", "linkedin_url", "portfolio_urls"}
    assert configured["target_titles"] == ["Senior Backend Engineer", "Backend Engineer"]
    assert configured["seniority_level"] == "senior"
    assert any("You can change them under Profile" in w for w in body["warnings"])

    db.expire_all()
    assert "python" in [s.lower() for s in profile.skills]
    assert profile.target_titles[0] == "Senior Backend Engineer"
    assert profile.linkedin_url.startswith("https://")
    assert "linkedin.com/in/janedoe" in profile.linkedin_url
    assert profile.portfolio_urls == ["https://github.com/janedoe"]
    assert profile.seniority_level == "senior"


def test_upload_never_overwrites_what_a_human_typed(client, auth_headers, profile, db):
    human_skills = list(profile.skills)
    human_titles = list(profile.target_titles)
    human_linkedin = profile.linkedin_url

    response = upload_resume(client, auth_headers)
    assert response.status_code == 201, response.text
    configured = response.json()["auto_configured"]
    assert "skills" not in configured
    assert "target_titles" not in configured
    assert "linkedin_url" not in configured

    db.expire_all()
    assert profile.skills == human_skills
    assert profile.target_titles == human_titles
    assert profile.linkedin_url == human_linkedin


def test_upload_never_touches_the_truthfulness_fields(client, auth_headers, profile, db):
    # The resume mentions "7+ years"; none of these may be inferred from it.
    response = upload_resume(client, auth_headers)
    assert response.status_code == 201, response.text
    configured = response.json()["auto_configured"]
    assert "years_experience" not in configured
    assert "requires_sponsorship" not in configured
    assert "min_salary_amount" not in configured

    db.expire_all()
    assert float(profile.years_experience) == 7
    assert profile.requires_sponsorship is False
    assert profile.min_salary_amount == 140000


def test_upload_proposed_facts_stay_unverified(client, auth_headers, profile):
    response = upload_resume(client, auth_headers)
    assert response.status_code == 201, response.text
    assert response.json()["proposed_fact_count"] > 0

    rows = client.get("/api/v1/facts", headers=auth_headers).json()
    assert rows
    # The invariant: auto-configure must not have verified anything either.
    assert all(row["verified"] is False for row in rows)


# -------------------------------------------------------------------- auth
def test_endpoints_require_authentication(client):
    assert client.get("/api/v1/autopilot/status").status_code == 401
    assert client.post("/api/v1/autopilot/run", json={}).status_code == 401


def test_seed_placeholders_count_as_empty_for_auto_configure(client, auth_headers, profile, db):
    """ "[SKILL 1]" is an instruction to the human, not a statement by them.

    The seed profile ships bracketed placeholders; refusing to replace those
    would leave a fresh install unable to configure itself from the resume it
    was just given, while a real value the human typed must still never move.
    """
    profile.skills = ["[SKILL 1]", "[SKILL 2]"]
    profile.target_titles = ["[TARGET TITLE 1]"]
    profile.linkedin_url = ""
    db.flush()

    response = upload_resume(client, auth_headers)
    assert response.status_code == 201, response.text
    configured = response.json()["auto_configured"]
    assert "skills" in configured
    assert "target_titles" in configured

    db.expire_all()
    assert "[SKILL 1]" not in profile.skills
    assert "python" in [s.lower() for s in profile.skills]
    assert profile.target_titles[0] == "Senior Backend Engineer"


def test_stale_scores_rebuild_when_the_profile_changes(
    client, auth_headers, profile, facts, db, job
):
    """Upload -> verify -> run must never rank against the pre-upload profile."""
    first = client.post(
        "/api/v1/autopilot/run", headers=auth_headers, json={"include_drafting": False}
    ).json()
    assert first["scoring"]["scored"] >= 1

    # A second run with nothing changed re-scores nothing: matches are current.
    unchanged = client.post(
        "/api/v1/autopilot/run", headers=auth_headers, json={"include_drafting": False}
    ).json()
    assert unchanged["scoring"]["scored"] == 0

    # The human materially changes the profile; the old scores describe someone
    # who no longer exists on file, so the next run rebuilds them all.
    response = client.put(
        "/api/v1/profile", headers=auth_headers, json={"skills": ["rust", "embedded"]}
    )
    assert response.status_code == 200

    rebuilt = client.post(
        "/api/v1/autopilot/run", headers=auth_headers, json={"include_drafting": False}
    ).json()
    assert rebuilt["scoring"]["scored"] >= 1
