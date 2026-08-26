"""Dashboard buckets: "what is the agent doing, and what is blocking it".

A count on its own is not an answer -- "3 failed" tells the user nothing they
can act on. These tests pin the exact reason strings and the empty-state
explanation, because those are the part that makes the dashboard useful.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.enums import (
    ApplicationStatus,
    DocumentKind,
    MatchDecision,
    ReviewReason,
    ReviewStatus,
)
from app.models.application import Application, ReviewTask
from app.models.job import JobMatch, JobSourceSubscription
from app.models.profile import Document
from tests.conftest import make_job

pytestmark = pytest.mark.integration

#: Fields other parts of the UI already read. Adding buckets must not drop one.
EXISTING_FIELDS = (
    "automation_enabled",
    "global_automation_enabled",
    "paused_reason",
    "applications_today",
    "daily_application_limit",
    "auto_submit_min_score",
    "new_matches",
    "shortlisted",
    "awaiting_review",
    "auto_submitted",
    "rejected_or_skipped",
    "pipeline",
    "unread_notifications",
    "llm_mode",
    "top_matches",
    "recent_activity",
    "rejection_reasons",
)


def dashboard(client, auth_headers) -> dict:
    response = client.get("/api/v1/dashboard", headers=auth_headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_existing_dashboard_fields_survive(client, auth_headers):
    body = dashboard(client, auth_headers)
    for field in EXISTING_FIELDS:
        assert field in body, f"DashboardOut lost '{field}'"
    assert "buckets" in body
    assert "empty_state" in body


def test_all_six_buckets_are_always_present(client, auth_headers):
    buckets = dashboard(client, auth_headers)["buckets"]
    assert set(buckets) == {
        "new_jobs_found",
        "high_match",
        "queued_for_auto",
        "needs_review",
        "submitted",
        "failed_or_stopped",
    }
    for name, bucket in buckets.items():
        assert bucket["count"] == 0, name
        assert bucket["link"].startswith("/"), name


def test_buckets_count_scoring_drafting_and_failure(
    client, auth_headers, db, user, profile, facts, job
):
    # A second job so the shortlist has more than one row to reason about.
    second = make_job(title="Backend Engineer", company="Contoso")
    db.add(second)
    db.flush()

    assert client.post("/api/v1/matches/rescore", headers=auth_headers).status_code == 200
    drafted = client.post(
        "/api/v1/applications/draft", headers=auth_headers, json={"job_id": str(job.id)}
    )
    assert drafted.status_code == 201, drafted.text

    body = dashboard(client, auth_headers)
    buckets = body["buckets"]
    assert buckets["new_jobs_found"]["count"] == 2
    # Nothing is authorized, so the draft lands in review rather than the queue.
    assert buckets["queued_for_auto"]["count"] == 0
    assert buckets["needs_review"]["count"] >= 1
    assert buckets["submitted"]["count"] == 0
    assert buckets["failed_or_stopped"]["count"] == 0

    # high_match counts shortlisted matches at or above the auto-submit score.
    expected_high = sum(
        1
        for m in db.execute(select(JobMatch).where(JobMatch.user_id == user.id)).scalars()
        if m.decision == MatchDecision.SHORTLISTED.value
        and m.dismissed_at is None
        and m.score >= body["auto_submit_min_score"]
    )
    assert expected_high >= 1
    assert buckets["high_match"]["count"] == expected_high
    assert buckets["high_match"]["link"] == f"/jobs?min_score={body['auto_submit_min_score']}"

    # Force one application to a stopped state with an explicit reason, the way
    # a submission error would.
    application = db.get(Application, drafted.json()["application"]["id"])
    application.status = ApplicationStatus.FAILED.value
    db.add(
        ReviewTask(
            user_id=user.id,
            application_id=application.id,
            job_id=job.id,
            reason=ReviewReason.SUBMISSION_ERROR.value,
            status=ReviewStatus.OPEN.value,
            title="Submission failed",
        )
    )
    db.flush()

    after = dashboard(client, auth_headers)["buckets"]
    assert after["failed_or_stopped"]["count"] == 1
    reasons = {row["reason"]: row for row in after["failed_or_stopped"]["failure_reasons"]}
    assert ReviewReason.SUBMISSION_ERROR.value in reasons
    assert reasons[ReviewReason.SUBMISSION_ERROR.value]["label"] == "Submission error"


def test_failure_reasons_group_by_reason_with_labels(client, auth_headers, db, user):
    jobs = [make_job(external_id=f"stopped:{i}", title=f"Role {i}") for i in range(3)]
    db.add_all(jobs)
    db.flush()

    stopped = [
        (ApplicationStatus.FAILED.value, ReviewReason.SUBMISSION_ERROR.value),
        (
            ApplicationStatus.BLOCKED_BY_POLICY.value,
            ReviewReason.PLATFORM_PROHIBITS_AUTOMATION.value,
        ),
        (ApplicationStatus.CANCELLED.value, ReviewReason.SUBMISSION_ERROR.value),
    ]
    for row, (status, reason) in zip(jobs, stopped, strict=True):
        application = Application(user_id=user.id, job_id=row.id, status=status)
        db.add(application)
        db.flush()
        db.add(
            ReviewTask(
                user_id=user.id,
                application_id=application.id,
                job_id=row.id,
                reason=reason,
                status=ReviewStatus.OPEN.value,
            )
        )
    db.flush()

    bucket = dashboard(client, auth_headers)["buckets"]["failed_or_stopped"]
    assert bucket["count"] == 3
    grouped = {row["reason"]: row for row in bucket["failure_reasons"]}
    assert grouped[ReviewReason.SUBMISSION_ERROR.value]["count"] == 2
    assert grouped[ReviewReason.SUBMISSION_ERROR.value]["label"] == "Submission error"
    prohibited = grouped[ReviewReason.PLATFORM_PROHIBITS_AUTOMATION.value]
    assert prohibited["count"] == 1
    assert prohibited["label"] == "Platform prohibits automation"


def test_submitted_bucket_counts_submissions_in_the_window(client, auth_headers, db, user, job):
    application = Application(
        user_id=user.id,
        job_id=job.id,
        status=ApplicationStatus.SUBMITTED.value,
        submitted_at=datetime.now(UTC),
    )
    db.add(application)
    db.flush()
    assert dashboard(client, auth_headers)["buckets"]["submitted"]["count"] == 1


def test_empty_state_explains_a_fresh_account(client, auth_headers):
    empty = dashboard(client, auth_headers)["empty_state"]
    assert empty["is_empty"] is True
    assert empty["has_sources"] is False
    assert empty["has_verified_facts"] is False
    assert empty["has_resume"] is False
    assert empty["message"]
    assert empty["next_step"]
    assert "resume" in empty["next_step"].lower()


def test_empty_state_tracks_what_the_user_has_set_up(
    client, auth_headers, db, user, profile, facts, job
):
    db.add(
        Document(
            user_id=user.id,
            kind=DocumentKind.RESUME_SOURCE.value,
            filename="resume.pdf",
            sha256="a" * 64,
        )
    )
    db.add(
        JobSourceSubscription(
            user_id=user.id, connector_key="greenhouse", identifier="northwind", enabled=True
        )
    )
    db.flush()

    still_empty = dashboard(client, auth_headers)["empty_state"]
    assert still_empty["is_empty"] is True
    assert still_empty["has_sources"] is True
    assert still_empty["has_verified_facts"] is True
    assert still_empty["has_resume"] is True
    assert "Autopilot" in still_empty["next_step"]

    assert client.post("/api/v1/matches/rescore", headers=auth_headers).status_code == 200
    filled = dashboard(client, auth_headers)["empty_state"]
    assert filled["is_empty"] is False
    assert filled["message"]
    assert filled["next_step"]
