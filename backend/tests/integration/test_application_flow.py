"""Drafting, the policy gate, authorization, and the assistant hand-off."""

from __future__ import annotations

import pytest

from app.schemas.settings import AUTHORIZATION_ACKNOWLEDGEMENT

pytestmark = pytest.mark.integration


def _draft(client, auth_headers, job):
    return client.post(
        "/api/v1/applications/draft",
        headers=auth_headers,
        json={"job_id": str(job.id), "include_cover_letter": True},
    )


def test_draft_goes_to_review_when_the_platform_is_not_authorized(
    client, auth_headers, profile, facts, job
):
    response = _draft(client, auth_headers, job)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["application"]["status"] == "needs_review"
    assert body["policy"]["may_submit"] is False
    assert "platform_not_authorized" in body["policy"]["review_reasons"]
    assert body["review_task_id"]

    review = client.get(f"/api/v1/reviews/{body['review_task_id']}", headers=auth_headers).json()
    assert review["status"] == "open"
    assert review["action_url"] == job.apply_url
    assert review["draft_payload"]["prefilled_fields"]


def test_draft_generates_documents_that_pass_the_fact_guard(
    client, auth_headers, profile, facts, job
):
    application_id = _draft(client, auth_headers, job).json()["application"]["id"]
    detail = client.get(f"/api/v1/applications/{application_id}", headers=auth_headers).json()

    roles = {d["role"] for d in detail["documents"]}
    assert roles == {"resume", "cover_letter"}
    blocking = [f for f in detail["fact_guard_flags"] if f["severity"] == "block"]
    assert blocking == [], blocking

    documents = client.get("/api/v1/documents?kind=resume_generated", headers=auth_headers).json()
    content = client.get(
        f"/api/v1/documents/{documents[0]['id']}/content", headers=auth_headers
    ).text
    assert "Northwind Systems" in content  # a verified employer
    assert "AWS Certified" not in content  # the unverified certification


def test_required_questions_the_agent_cannot_answer_block_approval(
    client, auth_headers, profile, facts, job
):
    profile.requires_sponsorship = None  # now unanswerable
    application_id = _draft(client, auth_headers, job).json()["application"]["id"]

    response = client.post(
        f"/api/v1/applications/{application_id}/approve", headers=auth_headers, json={"note": ""}
    )
    assert response.status_code == 400
    assert "Answer the required questions first" in response.text


def test_answering_then_approving_works(client, auth_headers, profile, facts, job):
    profile.requires_sponsorship = None
    application_id = _draft(client, auth_headers, job).json()["application"]["id"]
    detail = client.get(f"/api/v1/applications/{application_id}", headers=auth_headers).json()
    unanswered = [a for a in detail["answers"] if a["required"] and a["needs_human"]]
    assert unanswered

    client.patch(
        f"/api/v1/applications/{application_id}/answers",
        headers=auth_headers,
        json=[{"answer_id": a["id"], "answer_value": "No"} for a in unanswered],
    )
    approved = client.post(
        f"/api/v1/applications/{application_id}/approve",
        headers=auth_headers,
        json={"note": "checked"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"


# ------------------------------------------------------------- authorization
def test_authorization_requires_the_exact_acknowledgement(client, auth_headers):
    bad = client.post(
        "/api/v1/settings/authorizations",
        headers=auth_headers,
        json={
            "platform_key": "greenhouse",
            "policy": "auto_submit",
            "acknowledgement": "sure, go ahead",
        },
    )
    assert bad.status_code == 422
    assert "does not match" in bad.text


def test_authorization_can_be_granted_and_revoked(client, auth_headers):
    granted = client.post(
        "/api/v1/settings/authorizations",
        headers=auth_headers,
        json={
            "platform_key": "greenhouse",
            "policy": "auto_submit",
            "acknowledgement": AUTHORIZATION_ACKNOWLEDGEMENT,
        },
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()["is_active"] is True

    revoked = client.delete("/api/v1/settings/authorizations/greenhouse", headers=auth_headers)
    assert revoked.status_code == 200
    assert revoked.json()["is_active"] is False
    assert revoked.json()["policy"] == "review_required"


@pytest.mark.parametrize("platform", ["linkedin", "indeed", "naukri"])
def test_prohibited_platforms_cannot_be_authorized_at_all(client, auth_headers, platform):
    response = client.post(
        "/api/v1/settings/authorizations",
        headers=auth_headers,
        json={
            "platform_key": platform,
            "policy": "auto_submit",
            "acknowledgement": AUTHORIZATION_ACKNOWLEDGEMENT,
        },
    )
    assert response.status_code == 403
    assert "prohibits automated applications" in response.text


def test_review_only_policy_is_rejected_by_the_grant_endpoint(client, auth_headers):
    response = client.post(
        "/api/v1/settings/authorizations",
        headers=auth_headers,
        json={
            "platform_key": "greenhouse",
            "policy": "review_required",
            "acknowledgement": AUTHORIZATION_ACKNOWLEDGEMENT,
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize("platform", ["adzuna", "careers_page", "manual", "rss"])
def test_discovery_only_platforms_cannot_be_authorized_for_submission(
    client, auth_headers, platform
):
    """These connectors read jobs happily; none of them has a form we can drive."""
    response = client.post(
        "/api/v1/settings/authorizations",
        headers=auth_headers,
        json={
            "platform_key": platform,
            "policy": "auto_submit",
            "acknowledgement": AUTHORIZATION_ACKNOWLEDGEMENT,
        },
    )
    assert response.status_code == 403, response.text
    assert "discovery and review only" in response.text


@pytest.mark.parametrize(
    "platform", ["greenhouse", "lever", "ashby", "workable", "smartrecruiters"]
)
def test_every_supported_platform_can_be_authorized(client, auth_headers, platform):
    response = client.post(
        "/api/v1/settings/authorizations",
        headers=auth_headers,
        json={
            "platform_key": platform,
            "policy": "auto_submit",
            "acknowledgement": AUTHORIZATION_ACKNOWLEDGEMENT,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is True


def test_pause_is_instant_and_audited(client, auth_headers):
    paused = client.post(
        "/api/v1/settings/pause", headers=auth_headers, json={"reason": "stop everything"}
    )
    assert paused.status_code == 200
    assert paused.json()["automation_enabled"] is False

    entries = client.get("/api/v1/audit?action=automation.paused", headers=auth_headers).json()
    assert entries["total"] >= 1
    assert entries["items"][0]["payload"]["reason"] == "stop everything"


# ---------------------------------------------------------------------------
# The critic is the counterpart to fact_guard: it reports what the document
# left OUT, and unlike the guard it may never block.
# ---------------------------------------------------------------------------
def test_drafting_attaches_an_advisory_critique(client, auth_headers, profile, facts, job):
    application_id = _draft(client, auth_headers, job).json()["application"]["id"]
    detail = client.get(f"/api/v1/applications/{application_id}", headers=auth_headers).json()

    assert "critique" in detail, "the critique must reach the API, not just the row"
    assert "resume" in detail["critique"]
    assert "cover_letter" in detail["critique"]

    resume_critique = detail["critique"]["resume"]
    assert 0 <= resume_critique["score"] <= 100
    assert 0.0 <= resume_critique["keyword_coverage"] <= 1.0
    assert resume_critique["word_count"] > 0
    assert isinstance(resume_critique["findings"], list)


def test_the_critique_never_blocks_an_application(client, auth_headers, profile, facts, job):
    """fact_guard can stop an application. The critic advises and nothing more."""
    response = _draft(client, auth_headers, job)
    application_id = response.json()["application"]["id"]
    detail = client.get(f"/api/v1/applications/{application_id}", headers=auth_headers).json()

    # It ran and has an opinion...
    assert detail["critique"]["resume"]["findings"] is not None
    # ...and the application's fate is unchanged by it.
    assert detail["status"] != "blocked"
    assert detail["validation_errors"] == []


def test_the_critique_only_ever_suggests_facts_the_candidate_owns(
    client, auth_headers, profile, facts, job
):
    """The anti-fabrication guarantee, asserted through the real pipeline.

    A critic that could suggest arbitrary keywords would be a fabrication vector
    wearing a helpful hat, so every suggestion must trace to a stored string.
    """
    application_id = _draft(client, auth_headers, job).json()["application"]["id"]
    detail = client.get(f"/api/v1/applications/{application_id}", headers=auth_headers).json()

    owned = {
        s.lower() for s in client.get("/api/v1/profile", headers=auth_headers).json()["skills"]
    }
    for fact in client.get("/api/v1/facts", headers=auth_headers).json():
        if fact["verified"]:
            owned |= {t.lower() for t in fact.get("tags") or []}

    for report in detail["critique"].values():
        for finding in report["findings"]:
            if finding.get("suggestion"):
                assert finding["suggestion"].lower() in owned, finding
