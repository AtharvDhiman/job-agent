"""The audit trail must be tamper-evident, and your data must be exportable."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.audit import AuditLog
from app.services import audit as audit_service

pytestmark = pytest.mark.integration


def test_audit_chain_is_verifiable(client, auth_headers, profile, facts, job):
    client.post("/api/v1/matches/rescore", headers=auth_headers)
    client.post("/api/v1/applications/draft", headers=auth_headers, json={"job_id": str(job.id)})

    result = client.get("/api/v1/audit/verify", headers=auth_headers).json()
    assert result["valid"] is True
    assert result["checked"] > 0
    assert result["broken_at_seq"] is None


def test_tampering_with_an_entry_breaks_the_chain(client, auth_headers, profile, db):
    client.put("/api/v1/profile", headers=auth_headers, json={"headline": "one"})
    client.put("/api/v1/profile", headers=auth_headers, json={"headline": "two"})

    entry = db.execute(select(AuditLog).order_by(AuditLog.seq.asc())).scalars().first()
    entry.payload = {"tampered": True}
    db.flush()

    result = audit_service.verify_chain(db)
    assert result["valid"] is False
    assert result["broken_at_seq"] == entry.seq
    assert "do not match" in result["detail"]


def test_there_is_no_route_that_edits_or_deletes_audit_entries(client):
    from app.main import app

    audit_paths = [p for p in app.openapi()["paths"] if "/audit" in p]
    for path in audit_paths:
        methods = set(app.openapi()["paths"][path])
        assert methods <= {"get"}, (path, methods)


def test_every_sensitive_action_is_audited(client, auth_headers, profile, facts, job):
    client.post("/api/v1/matches/rescore", headers=auth_headers)
    client.post("/api/v1/applications/draft", headers=auth_headers, json={"job_id": str(job.id)})
    client.post("/api/v1/settings/pause", headers=auth_headers, json={"reason": "test"})

    actions = {
        item["action"]
        for item in client.get("/api/v1/audit?limit=200", headers=auth_headers).json()["items"]
    }
    for expected in (
        "user.login",
        "match.scored",
        "application.drafted",
        "review.created",
        "document.generated",
        "automation.paused",
    ):
        assert expected in actions, (expected, sorted(actions))


def test_audit_payloads_redact_secrets(db, user):
    entry = audit_service.record(
        db, "test.action", user_id=user.id, payload={"password": "hunter2", "keep": "visible"}
    )
    assert entry.payload["password"] == "[redacted]"
    assert entry.payload["keep"] == "visible"


def test_export_contains_every_record_type(client, auth_headers, profile, facts, job):
    client.post("/api/v1/matches/rescore", headers=auth_headers)
    client.post("/api/v1/applications/draft", headers=auth_headers, json={"job_id": str(job.id)})

    response = client.get("/api/v1/privacy/export", headers=auth_headers)
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    payload = response.json()

    for key in (
        "user",
        "profile",
        "career_facts",
        "documents",
        "matches",
        "applications",
        "review_tasks",
        "notifications",
        "audit_log",
    ):
        assert key in payload, key
    assert payload["user"]["hashed_password"] == "[not exported]"
    assert payload["career_facts"]


def test_erase_requires_the_exact_confirmation(client, auth_headers, profile):
    response = client.post(
        "/api/v1/privacy/erase", headers=auth_headers, json={"confirmation": "yes please"}
    )
    assert response.status_code == 422


def test_erase_removes_data_but_keeps_the_chain_verifiable(
    client, auth_headers, profile, facts, job, db
):
    client.post("/api/v1/matches/rescore", headers=auth_headers)
    client.post("/api/v1/applications/draft", headers=auth_headers, json={"job_id": str(job.id)})

    response = client.post(
        "/api/v1/privacy/erase", headers=auth_headers, json={"confirmation": "DELETE MY DATA"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["erased"] is True
    assert body["rows_deleted"]["career_facts"] > 0
    assert body["audit_entries_anonymised"] > 0

    assert audit_service.verify_chain(db)["valid"] is True
    remaining = db.execute(select(AuditLog).where(AuditLog.user_id.is_not(None))).scalars().all()
    assert all(e.actor != "owner@example.com" for e in remaining)
