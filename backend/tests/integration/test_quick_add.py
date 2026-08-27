"""Quick-add: paste a job from anywhere, agent extracts + scores + drafts."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

JOB_TEXT = (
    "We are hiring a Senior Backend Engineer. You will work with Python, PostgreSQL, "
    "Docker, Kubernetes and AWS. Strong SQL required. 5+ years of experience. "
    "The salary range is $150,000 - $190,000 per year. Visa sponsorship is available."
)


def quick_add(client, headers, **overrides):
    body = {
        "url": "https://www.linkedin.com/jobs/view/123456",
        "company": "Meridian Cloud",
        "title": "Senior Backend Engineer",
        "description_text": JOB_TEXT,
        "location_raw": "Remote - US",
        "draft": True,
    }
    body.update(overrides)
    return client.post("/api/v1/jobs/quick-add", headers=headers, json=body)


def test_quick_add_extracts_scores_and_drafts(client, auth_headers, profile, facts):
    response = quick_add(client, auth_headers)
    assert response.status_code == 201, response.text
    data = response.json()

    # Extraction happened from the pasted text, no fields typed by the user.
    assert data["score"] is not None
    assert "python" in data["matching_skills"]
    assert "postgresql" in data["matching_skills"]

    # A full application was drafted and is waiting in review.
    assert data["application_id"] is not None
    assert data["application_status"] in ("needs_review", "queued")


def test_quick_add_populates_the_job_from_pasted_text(client, auth_headers, profile):
    response = quick_add(client, auth_headers, draft=False)
    assert response.status_code == 201, response.text
    job = client.get(f"/api/v1/jobs/{response.json()['job_id']}", headers=auth_headers).json()

    assert job["seniority"] == "senior"
    assert job["work_arrangement"] == "remote"
    assert job["salary_min"] == 150000 and job["salary_currency"] == "USD"
    assert job["visa_sponsorship_mentioned"] is True
    assert "kubernetes" in job["extracted_skills"]


def test_quick_add_appears_on_the_companies_view(client, auth_headers, profile):
    quick_add(client, auth_headers, company="Brand New Co", draft=False)
    companies = client.get("/api/v1/companies?q=Brand New Co", headers=auth_headers).json()
    assert companies["total"] == 1
    assert companies["items"][0]["company"] == "Brand New Co"


def test_quick_add_without_draft_only_scores(client, auth_headers, profile):
    data = quick_add(client, auth_headers, draft=False).json()
    assert data["score"] is not None
    assert data["application_id"] is None


def test_quick_add_requires_the_operator_role(client, auth_headers, profile, db, user):
    from app.core.security import Role

    user.role = Role.VIEWER.value
    db.flush()
    assert quick_add(client, auth_headers).status_code == 403


def test_quick_add_requires_auth(client):
    assert client.post("/api/v1/jobs/quick-add", json={}).status_code == 401


# ---------------------------------------------------------------------------
# Attribution. Quick-add used to file every pasted job as `manual`, throwing
# away the one fact the URL reliably carries.
# ---------------------------------------------------------------------------
def test_a_pasted_naukri_job_is_recorded_as_naukri(client, auth_headers, profile, db):
    from sqlalchemy import select

    from app.models.job import Job

    response = quick_add(
        client,
        auth_headers,
        url="https://www.naukri.com/job-listings-senior-backend-engineer-acme-1",
    )
    assert response.status_code == 201, response.text

    job = db.execute(select(Job).order_by(Job.created_at.desc())).scalars().first()
    assert job.connector_key == "naukri"
    assert job.submission_policy_default == "prohibited"


def test_a_pasted_naukri_job_can_never_be_handed_to_the_assistant(
    client, auth_headers, profile, facts, db, user
):
    """The end the user actually cares about: it is drafted, never submitted."""
    from sqlalchemy import select

    from app.models.job import Job
    from app.services import policy

    assert (
        quick_add(
            client,
            auth_headers,
            url="https://www.naukri.com/job-listings-senior-backend-engineer-acme-1",
        ).status_code
        == 201
    )
    job = db.execute(select(Job).order_by(Job.created_at.desc())).scalars().first()

    assert policy.is_hard_prohibited(job.connector_key) is True

    # Hand decide() the most permissive inputs that exist: a full auto-submit
    # grant, a perfect score, the kill-switch on, nothing else objecting. The
    # prohibition has to survive all of it, because every other guard here is
    # something a user can switch off.
    from datetime import UTC, datetime

    from sqlalchemy import select as _select

    from app.core.enums import SubmissionPolicy
    from app.models.user import AgentSettings, PlatformAuthorization

    agent_settings = db.execute(
        _select(AgentSettings).where(AgentSettings.user_id == user.id)
    ).scalar_one()

    decision = policy.decide(
        job=job,
        connector_policy=job.submission_policy_default,
        authorization=PlatformAuthorization(
            platform_key="naukri",
            policy=SubmissionPolicy.AUTO_SUBMIT.value,
            granted_at=datetime.now(UTC),
            revoked_at=None,
        ),
        agent_settings=agent_settings,
        score=100,
        global_enabled=True,
        applications_today=0,
    )
    assert decision.may_submit is False
    assert decision.policy == SubmissionPolicy.PROHIBITED.value


def test_a_pasted_greenhouse_job_keeps_its_ats_identity(client, auth_headers, profile, db):
    """Attribution cuts both ways: a real ATS link is not anonymous either."""
    from sqlalchemy import select

    from app.models.job import Job

    assert (
        quick_add(
            client, auth_headers, url="https://boards.greenhouse.io/acme/jobs/4001"
        ).status_code
        == 201
    )
    job = db.execute(select(Job).order_by(Job.created_at.desc())).scalars().first()
    assert job.connector_key == "greenhouse"
    # Recognising the form is not permission to submit it.
    assert job.submission_policy_default == "review_required"


def test_an_unrecognised_url_is_still_manual(client, auth_headers, profile, db):
    from sqlalchemy import select

    from app.models.job import Job

    assert (
        quick_add(client, auth_headers, url="https://careers.example.com/roles/9").status_code
        == 201
    )
    job = db.execute(select(Job).order_by(Job.created_at.desc())).scalars().first()
    assert job.connector_key == "manual"
