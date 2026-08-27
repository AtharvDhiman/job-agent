"""The browser assistant surface: what it may receive, and where it must stop."""

from __future__ import annotations

import base64

import pytest

from app.core.config import settings
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


def draft(client, auth_headers, job):
    return client.post(
        "/api/v1/applications/draft", headers=auth_headers, json={"job_id": str(job.id)}
    ).json()


def ready(client, auth_headers, job, policy="auto_submit"):
    """Score, authorize, resume automation, then draft. Returns the draft body.

    Scoring first matters: an unscored job carries a score of 0, which is below
    any sensible auto-submit threshold and correctly lands in review.
    """
    client.post("/api/v1/matches/rescore", headers=auth_headers)
    authorize(client, auth_headers, policy=policy)
    client.post("/api/v1/settings/resume", headers=auth_headers)
    return draft(client, auth_headers, job)


def test_assistant_requires_its_token(client):
    assert client.get("/api/v1/assistant/tasks/next").status_code == 401
    bad = client.get("/api/v1/assistant/tasks/next", headers={"X-Assistant-Token": "wrong"})
    assert bad.status_code == 401


def test_no_task_while_the_global_switch_is_off(
    client, auth_headers, assistant_headers, profile, facts, job
):
    ready(client, auth_headers, job)
    assert client.get("/api/v1/assistant/tasks/next", headers=assistant_headers).json() is None


def test_no_task_when_the_platform_is_not_authorized(
    client, auth_headers, assistant_headers, profile, facts, job, automation_on
):
    client.post("/api/v1/settings/resume", headers=auth_headers)
    draft(client, auth_headers, job)
    assert client.get("/api/v1/assistant/tasks/next", headers=assistant_headers).json() is None


def test_authorized_high_score_task_is_handed_out(
    client, auth_headers, assistant_headers, profile, facts, job, automation_on
):
    body = ready(client, auth_headers, job)
    assert body["application"]["status"] == "queued", body["policy"]

    task = client.get("/api/v1/assistant/tasks/next", headers=assistant_headers).json()
    assert task is not None
    assert task["may_click_submit"] is True
    assert task["apply_url"] == job.apply_url
    assert task["guard_rules"]["never_solve_captcha"] is True
    assert task["guard_rules"]["headless_forbidden"] is True
    assert any(f["question_external_id"] == "email" for f in task["fields"])
    assert {a["role"] for a in task["attachments"]} >= {"resume"}


def test_assisted_autofill_never_authorizes_the_submit_click(
    client, auth_headers, assistant_headers, profile, facts, job, automation_on
):
    ready(client, auth_headers, job, policy="assisted_autofill")
    task = client.get("/api/v1/assistant/tasks/next", headers=assistant_headers).json()
    assert task["may_click_submit"] is False
    assert task["mode"] == "assisted_autofill"


@pytest.mark.parametrize(
    "abort_reason,expected",
    [
        ("captcha_detected", "captcha_detected"),
        ("login_required", "login_required"),
        ("bot_protection_detected", "bot_protection_detected"),
        ("robots_disallowed", "robots_disallowed"),
        ("free_text_question", "free_text_question"),
    ],
)
def test_every_hard_stop_becomes_a_review_task(
    client,
    auth_headers,
    assistant_headers,
    profile,
    facts,
    job,
    automation_on,
    abort_reason,
    expected,
):
    application_id = ready(client, auth_headers, job)["application"]["id"]
    client.get("/api/v1/assistant/tasks/next", headers=assistant_headers)

    result = client.post(
        f"/api/v1/assistant/tasks/{application_id}/result",
        headers=assistant_headers,
        json={
            "outcome": "aborted",
            "abort_reason": abort_reason,
            "error_message": f"stopped: {abort_reason}",
            "guard_findings": [{"marker": abort_reason}],
            "assistant_version": "1.0.0",
        },
    ).json()

    assert result["review_reason"] == expected
    assert result["application_status"] == "blocked_by_policy"
    review = client.get(f"/api/v1/reviews/{result['review_task_id']}", headers=auth_headers).json()
    assert review["reason"] == expected
    assert review["action_url"]
    assert review["status"] == "open"


def test_unknown_question_must_abort(
    client, auth_headers, assistant_headers, profile, facts, job, automation_on
):
    application_id = ready(client, auth_headers, job)["application"]["id"]
    client.get("/api/v1/assistant/tasks/next", headers=assistant_headers)

    response = client.post(
        f"/api/v1/assistant/tasks/{application_id}/questions",
        headers=assistant_headers,
        json={
            "questions": [
                {
                    "external_id": "q1",
                    "text": "Email address",
                    "type": "short_text",
                    "required": True,
                },
                {
                    "external_id": "q2",
                    "text": "Why do you want to work here?",
                    "type": "long_text",
                    "required": True,
                },
            ]
        },
    ).json()

    assert response["must_abort"] is True
    assert any(u["external_id"] == "q2" for u in response["unanswerable"])
    assert any(a["external_id"] == "q1" for a in response["answers"])


def test_successful_submission_is_recorded_with_a_receipt(
    client, auth_headers, assistant_headers, profile, facts, job, automation_on
):
    application_id = ready(client, auth_headers, job)["application"]["id"]
    client.get("/api/v1/assistant/tasks/next", headers=assistant_headers)

    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()
    result = client.post(
        f"/api/v1/assistant/tasks/{application_id}/result",
        headers=assistant_headers,
        json={
            "outcome": "submitted",
            "confirmation_number": "GH-99887766",
            "receipt": {"submitted_at": "2026-08-24T10:00:00Z"},
            "filled_fields": [{"name": "email", "ok": True}],
            "screenshot_base64": png,
            "assistant_version": "1.0.0",
        },
    ).json()
    assert result["application_status"] == "submitted"

    detail = client.get(f"/api/v1/applications/{application_id}", headers=auth_headers).json()
    assert detail["confirmation_number"] == "GH-99887766"
    assert detail["submitted_at"]
    assert detail["pipeline_stage"] == "applied"

    attempts = client.get(
        f"/api/v1/applications/{application_id}/attempts", headers=auth_headers
    ).json()
    assert attempts[-1]["outcome"] == "submitted"

    dashboard = client.get("/api/v1/dashboard", headers=auth_headers).json()
    assert dashboard["applications_today"] == 1
    assert dashboard["pipeline"]["applied"] == 1


def test_daily_limit_stops_further_submissions(
    client, auth_headers, assistant_headers, profile, facts, job, automation_on, db
):
    from tests.conftest import make_job

    application_id = ready(client, auth_headers, job)["application"]["id"]
    client.patch("/api/v1/settings", headers=auth_headers, json={"daily_application_limit": 1})
    client.get("/api/v1/assistant/tasks/next", headers=assistant_headers)
    client.post(
        f"/api/v1/assistant/tasks/{application_id}/result",
        headers=assistant_headers,
        json={"outcome": "submitted", "confirmation_number": "X1"},
    )

    second = make_job(external_id="second-job")
    db.add(second)
    db.flush()
    client.post("/api/v1/matches/rescore", headers=auth_headers)
    body = draft(client, auth_headers, second)
    assert body["application"]["status"] == "needs_review"
    assert "daily_limit_reached" in body["policy"]["review_reasons"]


# --------------------------------------------------------------------------
# What a human approval may and may not unlock.
#
# workflow.approve() sets approved_by_user_id on ANY application, including one
# whose review task exists precisely because the platform may never be
# automated. Hand-out must consult the gate, not the approval flag.
# --------------------------------------------------------------------------
def _prohibited_job(db, connector_key="linkedin"):
    from tests.conftest import make_job

    row = make_job(
        connector_key=connector_key,
        compliance_tier="partner_api",
        submission_policy_default="prohibited",
        external_id=f"{connector_key}:1",
        source_url=f"https://www.{connector_key}.com/jobs/view/1",
        apply_url=f"https://www.{connector_key}.com/jobs/view/1",
    )
    db.add(row)
    db.flush()
    return row


def _approve(client, auth_headers, application_id, note="looks good"):
    return client.post(
        f"/api/v1/applications/{application_id}/approve",
        headers=auth_headers,
        json={"note": note},
    )


@pytest.mark.parametrize("platform", ["linkedin", "indeed", "naukri"])
def test_approving_a_prohibited_application_never_hands_it_to_the_assistant(
    client, auth_headers, assistant_headers, profile, facts, db, automation_on, platform
):
    """LinkedIn and Indeed are never automated -- not even filled, not ever."""
    prohibited = _prohibited_job(db, platform)
    client.post("/api/v1/settings/resume", headers=auth_headers)
    client.post("/api/v1/matches/rescore", headers=auth_headers)

    body = draft(client, auth_headers, prohibited)
    assert body["policy"]["policy"] == "prohibited"
    assert body["application"]["status"] == "needs_review"

    _approve(client, auth_headers, body["application"]["id"])

    task = client.get("/api/v1/assistant/tasks/next", headers=assistant_headers).json()
    assert task is None, "a prohibited platform was handed to the browser assistant"


def test_approval_is_not_a_substitute_for_the_typed_platform_grant(
    client, auth_headers, assistant_headers, profile, facts, job, automation_on
):
    """assisted_autofill needs an explicit grant (COMPLIANCE.md section 2)."""
    client.post("/api/v1/settings/resume", headers=auth_headers)
    client.post("/api/v1/matches/rescore", headers=auth_headers)
    body = draft(client, auth_headers, job)
    assert "platform_not_authorized" in body["policy"]["review_reasons"]

    _approve(client, auth_headers, body["application"]["id"])

    task = client.get("/api/v1/assistant/tasks/next", headers=assistant_headers).json()
    assert task is None, "an unauthorized platform was handed to the browser assistant"


def test_pausing_automation_stops_an_already_approved_application(
    client, auth_headers, assistant_headers, profile, facts, job, automation_on
):
    application_id = ready(client, auth_headers, job)["application"]["id"]
    _approve(client, auth_headers, application_id)
    client.post("/api/v1/settings/pause", headers=auth_headers, json={"reason": "stop"})

    task = client.get("/api/v1/assistant/tasks/next", headers=assistant_headers).json()
    assert task is None, "the per-user pause was bypassed by a human approval"


# --------------------------------------------------------------------------
# report_result carries an application id chosen by the caller and the assistant
# token has no user scope, so the state check is the only thing standing between
# a shared secret and "this application was submitted".
# --------------------------------------------------------------------------
def test_a_policy_blocked_attempt_cannot_be_relabelled_as_submitted(
    client, auth_headers, assistant_headers, profile, facts, job, automation_on
):
    application_id = ready(client, auth_headers, job)["application"]["id"]
    client.get("/api/v1/assistant/tasks/next", headers=assistant_headers)
    client.post(
        f"/api/v1/assistant/tasks/{application_id}/result",
        headers=assistant_headers,
        json={
            "outcome": "aborted",
            "abort_reason": "captcha_detected",
            "guard_findings": [{"marker": "recaptcha"}],
        },
    )

    replay = client.post(
        f"/api/v1/assistant/tasks/{application_id}/result",
        headers=assistant_headers,
        json={"outcome": "submitted", "confirmation_number": "FAKE-1"},
    )
    assert replay.status_code == 409, replay.text

    detail = client.get(f"/api/v1/applications/{application_id}", headers=auth_headers).json()
    assert detail["status"] == "blocked_by_policy"
    assert detail["submitted_at"] is None
    assert detail["confirmation_number"] is None


def test_a_submission_cannot_be_reported_twice(
    client, auth_headers, assistant_headers, profile, facts, job, automation_on
):
    application_id = ready(client, auth_headers, job)["application"]["id"]
    client.get("/api/v1/assistant/tasks/next", headers=assistant_headers)
    first = client.post(
        f"/api/v1/assistant/tasks/{application_id}/result",
        headers=assistant_headers,
        json={"outcome": "submitted", "confirmation_number": "GH-1"},
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/v1/assistant/tasks/{application_id}/result",
        headers=assistant_headers,
        json={"outcome": "submitted", "confirmation_number": "GH-2"},
    )
    assert second.status_code == 409, second.text

    dashboard = client.get("/api/v1/dashboard", headers=auth_headers).json()
    assert dashboard["applications_today"] == 1


def test_an_application_never_handed_out_cannot_report_a_result(
    client, auth_headers, assistant_headers, profile, facts, job, automation_on
):
    application_id = draft(client, auth_headers, job)["application"]["id"]
    response = client.post(
        f"/api/v1/assistant/tasks/{application_id}/result",
        headers=assistant_headers,
        json={"outcome": "submitted", "confirmation_number": "NOPE"},
    )
    assert response.status_code == 409, response.text


def test_questions_are_only_answered_for_an_application_in_progress(
    client, auth_headers, assistant_headers, profile, facts, job, automation_on
):
    """The shared assistant secret must not be a read oracle for any draft."""
    application_id = draft(client, auth_headers, job)["application"]["id"]
    response = client.post(
        f"/api/v1/assistant/tasks/{application_id}/questions",
        headers=assistant_headers,
        json={
            "questions": [
                {
                    "external_id": "email",
                    "text": "Email address",
                    "type": "short_text",
                    "required": True,
                }
            ]
        },
    )
    assert response.status_code == 409, response.text


# --------------------------------------------------------------------------
# Hand-out bookkeeping: no double claim, no unbounded retries.
# --------------------------------------------------------------------------
def test_the_same_application_is_never_claimed_twice(
    client, auth_headers, profile, facts, job, automation_on, db, user
):
    import uuid as _uuid

    from app.models.application import Application
    from app.services import application_workflow as workflow

    application_id = ready(client, auth_headers, job)["application"]["id"]
    application = db.get(Application, _uuid.UUID(application_id))

    first = workflow.claim_for_attempt(db, application, mode="auto_submit")
    second = workflow.claim_for_attempt(db, application, mode="auto_submit")
    assert first is not None
    assert second is None, "two assistants could claim the same application"
    assert application.attempt_count == 1


def test_attempts_are_capped_and_the_application_retires(
    client, auth_headers, assistant_headers, profile, facts, job, automation_on, db
):
    import uuid as _uuid

    from app.models.application import Application
    from app.services import application_workflow as workflow

    application_id = ready(client, auth_headers, job)["application"]["id"]
    application = db.get(Application, _uuid.UUID(application_id))
    application.attempt_count = workflow.MAX_ATTEMPTS_PER_APPLICATION
    db.flush()

    task = client.get("/api/v1/assistant/tasks/next", headers=assistant_headers).json()
    assert task is None, "an exhausted application was handed out again"

    db.refresh(application)
    assert application.status == "failed"


def test_a_policy_blocked_application_is_not_re_queued_by_approving_the_review(
    client, auth_headers, assistant_headers, profile, facts, job, automation_on
):
    """NEVER_DO: 'Retry an attempt that stopped for a policy reason.'"""
    application_id = ready(client, auth_headers, job)["application"]["id"]
    client.get("/api/v1/assistant/tasks/next", headers=assistant_headers)
    result = client.post(
        f"/api/v1/assistant/tasks/{application_id}/result",
        headers=assistant_headers,
        json={
            "outcome": "aborted",
            "abort_reason": "captcha_detected",
            "guard_findings": [{"marker": "recaptcha"}],
        },
    ).json()

    approved = client.post(
        f"/api/v1/reviews/{result['review_task_id']}/approve",
        headers=auth_headers,
        json={"note": "I solved it myself"},
    )
    assert approved.status_code == 200, approved.text

    detail = client.get(f"/api/v1/applications/{application_id}", headers=auth_headers).json()
    assert detail["status"] == "blocked_by_policy"
    assert client.get("/api/v1/assistant/tasks/next", headers=assistant_headers).json() is None


def test_an_in_progress_application_is_not_re_queued_by_approving_it(
    client, auth_headers, assistant_headers, profile, facts, job, automation_on
):
    """After an assisted-autofill hand-off the human owns the page. Filling it
    a second time would apply twice."""
    application_id = ready(client, auth_headers, job, policy="assisted_autofill")["application"][
        "id"
    ]
    assert client.get("/api/v1/assistant/tasks/next", headers=assistant_headers).json()

    _approve(client, auth_headers, application_id)
    assert client.get("/api/v1/assistant/tasks/next", headers=assistant_headers).json() is None


def test_a_human_can_close_out_an_assisted_autofill_they_submitted(
    client, auth_headers, assistant_headers, profile, facts, job, automation_on
):
    """browser-assistant/README.md promises this route exists."""
    application_id = ready(client, auth_headers, job, policy="assisted_autofill")["application"][
        "id"
    ]
    client.get("/api/v1/assistant/tasks/next", headers=assistant_headers)

    marked = client.post(
        f"/api/v1/applications/{application_id}/mark-submitted",
        headers=auth_headers,
        json={"confirmation_number": "HUMAN-1", "note": "clicked submit myself"},
    )
    assert marked.status_code == 200, marked.text
    assert marked.json()["status"] == "submitted"

    detail = client.get(f"/api/v1/applications/{application_id}", headers=auth_headers).json()
    assert detail["pipeline_stage"] == "applied"
    assert detail["confirmation_number"] == "HUMAN-1"

    attempts = client.get(
        f"/api/v1/applications/{application_id}/attempts", headers=auth_headers
    ).json()
    assert attempts[-1]["outcome"] == "submitted_by_human"
    assert client.get("/api/v1/assistant/tasks/next", headers=assistant_headers).json() is None
