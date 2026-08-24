"""Two-user authorization matrix, auth hardening, and assistant scoping.

Every endpoint that takes an id is driven twice: once by the owner of the row
and once by a second, fully legitimate account. The second account must never
see or change the first account's data.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.enums import ApplicationStatus, AuditAction
from app.core.security import Role, hash_password
from app.models.application import ApplicationDocument, SubmissionAttempt
from app.models.audit import AuditLog
from app.models.profile import CandidateProfile, Document
from app.models.user import AgentSettings, User
from app.schemas.settings import AUTHORIZATION_ACKNOWLEDGEMENT

pytestmark = pytest.mark.integration

INTRUDER_PASSWORD = "Intruder-Sup3r-Passw0rd!"
VIEWER_PASSWORD = "Viewer-Sup3r-Passw0rd!"


def _make_account(db, email: str, password: str, role: str) -> User:
    row = User(
        email=email,
        hashed_password=hash_password(password),
        full_name=email.split("@")[0],
        role=role,
    )
    db.add(row)
    db.flush()
    db.add(AgentSettings(user_id=row.id))
    db.add(CandidateProfile(user_id=row.id, full_name="Other Person", contact_email=email))
    db.flush()
    return row


def _headers(client, email: str, password: str) -> dict:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def intruder(db) -> User:
    return _make_account(db, "intruder@example.com", INTRUDER_PASSWORD, Role.OWNER.value)


@pytest.fixture
def intruder_headers(client, intruder) -> dict:
    return _headers(client, intruder.email, INTRUDER_PASSWORD)


@pytest.fixture
def viewer(db) -> User:
    return _make_account(db, "viewer@example.com", VIEWER_PASSWORD, Role.VIEWER.value)


@pytest.fixture
def viewer_headers(client, viewer) -> dict:
    return _headers(client, viewer.email, VIEWER_PASSWORD)


@pytest.fixture
def victim_resources(client, auth_headers, profile, facts, job, db) -> dict:
    """Everything the first account owns, created through the real API."""
    client.post("/api/v1/matches/rescore", headers=auth_headers)
    draft = client.post(
        "/api/v1/applications/draft", headers=auth_headers, json={"job_id": str(job.id)}
    ).json()
    application_id = draft["application"]["id"]

    review = client.get("/api/v1/reviews", headers=auth_headers).json()["items"][0]
    match = client.get("/api/v1/jobs", headers=auth_headers).json()["items"][0]["match"]
    document = client.get("/api/v1/documents", headers=auth_headers).json()[0]
    fact = client.get("/api/v1/facts", headers=auth_headers).json()[0]
    source = client.post(
        "/api/v1/sources",
        headers=auth_headers,
        json={"connector_key": "greenhouse", "identifier": "victimco"},
    ).json()
    client.post("/api/v1/notifications/digest/send", headers=auth_headers)

    return {
        "application_id": application_id,
        "review_id": review["id"],
        "match_id": match["id"],
        "document_id": document["id"],
        "fact_id": fact["id"],
        "source_id": source["id"],
        "job_id": str(job.id),
    }


# ------------------------------------------------------------------ IDOR
def test_no_endpoint_taking_an_id_serves_another_users_row(
    client, intruder_headers, victim_resources
):
    r = victim_resources
    decision = {"note": "hijack"}

    cases = [
        ("get", f"/api/v1/applications/{r['application_id']}", None),
        ("get", f"/api/v1/applications/{r['application_id']}/attempts", None),
        ("post", f"/api/v1/applications/{r['application_id']}/approve", decision),
        ("post", f"/api/v1/applications/{r['application_id']}/reject", decision),
        (
            "post",
            f"/api/v1/applications/{r['application_id']}/stage",
            {"pipeline_stage": "interview", "note": ""},
        ),
        ("patch", f"/api/v1/applications/{r['application_id']}/answers", []),
        ("get", f"/api/v1/reviews/{r['review_id']}", None),
        ("post", f"/api/v1/reviews/{r['review_id']}/approve", decision),
        ("post", f"/api/v1/reviews/{r['review_id']}/reject", decision),
        ("get", f"/api/v1/documents/{r['document_id']}/content", None),
        ("delete", f"/api/v1/documents/{r['document_id']}", None),
        ("patch", f"/api/v1/facts/{r['fact_id']}", {"category": "skill", "value": "stolen"}),
        ("delete", f"/api/v1/facts/{r['fact_id']}", None),
        (
            "patch",
            f"/api/v1/sources/{r['source_id']}",
            {"connector_key": "greenhouse", "identifier": "hijacked"},
        ),
        ("delete", f"/api/v1/sources/{r['source_id']}", None),
        ("post", f"/api/v1/matches/{r['match_id']}/dismiss", None),
    ]

    for method, path, body in cases:
        call = getattr(client, method)
        response = (
            call(path, headers=intruder_headers)
            if body is None
            else call(path, headers=intruder_headers, json=body)
        )
        assert response.status_code == 404, f"{method.upper()} {path} -> {response.status_code}"


def test_collection_endpoints_never_leak_another_users_rows(
    client, intruder_headers, victim_resources
):
    for path in (
        "/api/v1/applications",
        "/api/v1/reviews?status=all",
        "/api/v1/notifications",
        "/api/v1/jobs",
    ):
        body = client.get(path, headers=intruder_headers).json()
        assert body["items"] == [], path
        assert body["total"] == 0, path

    assert client.get("/api/v1/documents", headers=intruder_headers).json() == []
    assert client.get("/api/v1/facts", headers=intruder_headers).json() == []
    assert client.get("/api/v1/sources", headers=intruder_headers).json() == []

    # The audit feed carries the intruder's own sign-in and nothing else: no row
    # about the victim's account, and no row pointing at the victim's objects.
    victim_ids = {
        victim_resources[key]
        for key in ("application_id", "review_id", "document_id", "fact_id", "source_id")
    }
    entries = client.get("/api/v1/audit", headers=intruder_headers).json()["items"]
    assert entries
    for entry in entries:
        assert entry["actor"] == "intruder@example.com", entry
        assert entry["object_id"] not in victim_ids, entry


def test_the_owner_can_still_reach_their_own_rows(client, auth_headers, victim_resources):
    r = victim_resources
    assert (
        client.get(f"/api/v1/applications/{r['application_id']}", headers=auth_headers).status_code
        == 200
    )
    assert client.get(f"/api/v1/reviews/{r['review_id']}", headers=auth_headers).status_code == 200
    assert (
        client.get(
            f"/api/v1/documents/{r['document_id']}/content", headers=auth_headers
        ).status_code
        == 200
    )
    assert (
        client.post(f"/api/v1/matches/{r['match_id']}/dismiss", headers=auth_headers).status_code
        == 200
    )


# ------------------------------------------------------------------ RBAC
def test_a_viewer_cannot_mutate_anything(client, viewer_headers, job):
    mutations = [
        ("put", "/api/v1/profile", {"full_name": "Viewer"}),
        ("post", "/api/v1/facts", {"category": "skill", "value": "python"}),
        ("post", "/api/v1/facts/verify", {"fact_ids": [str(uuid.uuid4())], "verified": True}),
        ("post", "/api/v1/sources", {"connector_key": "greenhouse", "identifier": "x"}),
        ("post", "/api/v1/discovery/run", None),
        ("post", "/api/v1/matches/rescore", None),
        ("post", "/api/v1/applications/draft", {"job_id": str(job.id)}),
        ("post", "/api/v1/notifications/read", None),
        ("post", "/api/v1/notifications/digest/send", None),
        ("patch", "/api/v1/settings", {"daily_application_limit": 999}),
        ("post", "/api/v1/settings/resume", None),
        (
            "post",
            "/api/v1/settings/authorizations",
            {
                "platform_key": "greenhouse",
                "policy": "auto_submit",
                "acknowledgement": AUTHORIZATION_ACKNOWLEDGEMENT,
            },
        ),
        ("delete", "/api/v1/settings/authorizations/greenhouse", None),
        ("post", "/api/v1/privacy/erase", {"confirmation": "DELETE MY DATA"}),
    ]
    for method, path, body in mutations:
        call = getattr(client, method)
        response = (
            call(path, headers=viewer_headers)
            if body is None
            else call(path, headers=viewer_headers, json=body)
        )
        assert response.status_code == 403, f"{method.upper()} {path} -> {response.status_code}"


def test_a_viewer_may_still_read_and_may_still_pause(client, viewer_headers):
    assert client.get("/api/v1/dashboard", headers=viewer_headers).status_code == 200
    assert client.get("/api/v1/settings", headers=viewer_headers).status_code == 200
    paused = client.post(
        "/api/v1/settings/pause", headers=viewer_headers, json={"reason": "kill switch"}
    )
    assert paused.status_code == 200
    assert paused.json()["automation_enabled"] is False


def test_audit_verify_is_owner_only(client, viewer_headers, auth_headers):
    assert client.get("/api/v1/audit/verify", headers=viewer_headers).status_code == 403
    assert client.get("/api/v1/audit/verify", headers=auth_headers).status_code == 200


# ------------------------------------------------------------------ auth
def test_failed_logins_actually_lock_the_account(client, user, db):
    """The counter and the audit row were rolled back with the 401 response."""
    from app.api.v1.auth import MAX_FAILED_LOGINS

    db.commit()
    for _ in range(MAX_FAILED_LOGINS):
        assert (
            client.post(
                "/api/v1/auth/login", json={"email": user.email, "password": "wrong-password"}
            ).status_code
            == 401
        )

    db.expire_all()
    row = db.get(User, user.id)
    assert row.locked_until is not None

    # The correct password is refused while the lock stands.
    locked = client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "Sup3r-Secret-Passw0rd!"},
    )
    assert locked.status_code == 423

    failures = (
        db.execute(select(AuditLog).where(AuditLog.action == AuditAction.USER_LOGIN_FAILED.value))
        .scalars()
        .all()
    )
    assert len(failures) == MAX_FAILED_LOGINS


def test_a_successful_login_clears_the_counter(client, user, db):
    db.commit()
    client.post("/api/v1/auth/login", json={"email": user.email, "password": "nope"})
    db.expire_all()
    assert db.get(User, user.id).failed_login_count == 1

    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": "Sup3r-Secret-Passw0rd!"},
        ).status_code
        == 200
    )
    db.expire_all()
    assert db.get(User, user.id).failed_login_count == 0


def test_registering_a_differently_cased_duplicate_is_a_conflict_not_a_500(client, user, db):
    db.commit()
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": user.email.upper(),
            "password": "Another-Sup3r-Passw0rd!",
            "full_name": "Impostor",
        },
    )
    assert response.status_code == 409


def test_an_access_token_is_not_accepted_as_a_refresh_token(client, auth_headers):
    access = auth_headers["Authorization"].split(" ", 1)[1]
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": access})
    assert response.status_code == 401


def test_replaying_a_rotated_refresh_token_kills_every_session(client, user, db):
    db.commit()
    first = client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "Sup3r-Secret-Passw0rd!"},
    ).json()

    rotated = client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert rotated.status_code == 200
    fresh = rotated.json()

    # The presented token is dead.
    replay = client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert replay.status_code == 401

    # ...and the reuse invalidated the pair that replaced it, too.
    after = client.post("/api/v1/auth/refresh", json={"refresh_token": fresh["refresh_token"]})
    assert after.status_code == 401


def test_logout_revokes_every_refresh_token_for_the_caller(client, user, db):
    db.commit()
    creds = [
        client.post(
            "/api/v1/auth/login",
            json={"email": user.email, "password": "Sup3r-Secret-Passw0rd!"},
        ).json()
        for _ in range(2)
    ]
    headers = {"Authorization": f"Bearer {creds[0]['access_token']}"}
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    for issued in creds:
        assert (
            client.post(
                "/api/v1/auth/refresh", json={"refresh_token": issued["refresh_token"]}
            ).status_code
            == 401
        )


def test_logout_body_is_ignored_and_cannot_touch_another_account(client, user, intruder, db):
    db.commit()
    victim = client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "Sup3r-Secret-Passw0rd!"},
    ).json()
    thief = client.post(
        "/api/v1/auth/login",
        json={"email": intruder.email, "password": INTRUDER_PASSWORD},
    ).json()

    # The intruder logs out and hands us the victim's refresh token in the body.
    assert (
        client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {thief['access_token']}"},
            json={"refresh_token": victim["refresh_token"]},
        ).status_code
        == 204
    )

    # The victim's session is untouched.
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": victim["refresh_token"]}
        ).status_code
        == 200
    )


# ------------------------------------------------------------- assistant
def test_the_assistant_cannot_download_a_document_it_was_not_handed(
    client, auth_headers, assistant_headers, profile, facts, job, db
):
    """The shared secret was a read-any-document credential."""
    draft = client.post(
        "/api/v1/applications/draft", headers=auth_headers, json={"job_id": str(job.id)}
    ).json()
    application_id = uuid.UUID(draft["application"]["id"])

    link = (
        db.execute(
            select(ApplicationDocument).where(ApplicationDocument.application_id == application_id)
        )
        .scalars()
        .first()
    )
    assert link is not None

    denied = client.get(
        f"/api/v1/assistant/documents/{link.document_id}", headers=assistant_headers
    )
    assert denied.status_code == 404

    # Once a task is genuinely open, the attachment for THAT task is readable.
    db.add(
        SubmissionAttempt(
            application_id=application_id,
            attempt_number=1,
            mode="assisted_autofill",
            outcome="pending",
            started_at=datetime.now(UTC),
        )
    )
    db.flush()
    allowed = client.get(
        f"/api/v1/assistant/documents/{link.document_id}", headers=assistant_headers
    )
    assert allowed.status_code == 200
    assert "attachment" in allowed.headers["content-disposition"]


def test_an_open_task_does_not_unlock_unrelated_documents(
    client, auth_headers, assistant_headers, intruder, profile, facts, job, db
):
    draft = client.post(
        "/api/v1/applications/draft", headers=auth_headers, json={"job_id": str(job.id)}
    ).json()
    db.add(
        SubmissionAttempt(
            application_id=uuid.UUID(draft["application"]["id"]),
            attempt_number=1,
            mode="assisted_autofill",
            outcome="pending",
            started_at=datetime.now(UTC),
        )
    )
    unrelated = Document(
        user_id=intruder.id,
        kind="resume_source",
        filename="private.pdf",
        content_type="application/pdf",
        storage_key="unused",
        sha256="f" * 64,
    )
    db.add(unrelated)
    db.flush()

    response = client.get(f"/api/v1/assistant/documents/{unrelated.id}", headers=assistant_headers)
    assert response.status_code == 404


def test_the_assistant_cannot_resolve_questions_without_an_open_task(
    client, auth_headers, assistant_headers, profile, facts, job
):
    draft = client.post(
        "/api/v1/applications/draft", headers=auth_headers, json={"job_id": str(job.id)}
    ).json()
    response = client.post(
        f"/api/v1/assistant/tasks/{draft['application']['id']}/questions",
        headers=assistant_headers,
        json={"questions": [{"external_id": "q1", "text": "Email address", "required": True}]},
    )
    assert response.status_code == 409


def test_an_unknown_assistant_document_id_is_a_404_not_a_500(client, assistant_headers):
    response = client.get(f"/api/v1/assistant/documents/{uuid.uuid4()}", headers=assistant_headers)
    assert response.status_code == 404


# ------------------------------------------------------------- data at rest
def test_resume_text_and_parsed_contacts_are_encrypted_at_rest(client, auth_headers, profile, db):
    from sqlalchemy import text as sql_text

    resume = (
        b"Jane Candidate\njane@example.com\n+1 415 555 0199\n"
        b"Senior Backend Engineer at Northwind Systems\n"
    )
    upload = client.post(
        "/api/v1/documents",
        headers=auth_headers,
        files={"file": ("cv.txt", resume, "text/plain")},
        data={"kind": "resume_source"},
    )
    assert upload.status_code == 201, upload.text

    row = db.execute(sql_text("SELECT extracted_text, parsed FROM documents LIMIT 1")).one()
    for column in row:
        assert column is None or column.startswith("enc:v1:"), column
        assert "555 0199" not in (column or "")
        assert "jane@example.com" not in (column or "")

    # ...and the application still reads it back in the clear.
    document = db.execute(select(Document)).scalars().first()
    assert "jane@example.com" in document.extracted_text


def test_an_unknown_document_kind_is_rejected(client, auth_headers, profile):
    response = client.post(
        "/api/v1/documents",
        headers=auth_headers,
        files={"file": ("cv.txt", b"hello", "text/plain")},
        data={"kind": "../../etc"},
    )
    assert response.status_code == 400
    assert "Unknown document kind" in response.json()["detail"]


def test_a_hostile_filename_cannot_rewrite_the_download_header(
    client, auth_headers, user, profile, db
):
    """`filename="{document.filename}"` was interpolated raw.

    A stored name carrying a quote reopened the parameter and a CR/LF split the
    response, so the row is planted directly rather than going through multipart
    encoding (which would percent-escape the payload before it ever landed).
    """
    from app.services import storage as storage_service

    hostile = 'evil"; filename="payload.exe\r\nX-Injected: yes'
    key = storage_service.build_key(user.id, "other", "a" * 64, "planted")
    storage_service.get_storage().write(key, b"hello there")
    document = Document(
        user_id=user.id,
        kind="other",
        filename=hostile,
        content_type="text/plain",
        storage_key=key,
        sha256="a" * 64,
        size_bytes=11,
    )
    db.add(document)
    db.flush()

    response = client.get(f"/api/v1/documents/{document.id}/content", headers=auth_headers)
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    # No CR/LF, so nothing can be promoted out of the value into a real header.
    assert "\r" not in disposition and "\n" not in disposition
    assert disposition.count('filename="') == 1
    assert '"' not in disposition.split('filename="')[1].split('"')[0]
    assert "x-injected" not in {k.lower() for k in response.headers}


def test_a_confirmation_number_never_lands_in_a_plaintext_notification(
    client, auth_headers, profile, facts, job, db
):
    from app.models.application import Application
    from app.models.application import SubmissionAttempt as Attempt
    from app.models.audit import Notification
    from app.services import application_workflow as workflow

    draft = client.post(
        "/api/v1/applications/draft", headers=auth_headers, json={"job_id": str(job.id)}
    ).json()
    application = db.get(Application, uuid.UUID(draft["application"]["id"]))
    application.status = ApplicationStatus.IN_PROGRESS.value
    attempt = Attempt(
        application_id=application.id, attempt_number=1, mode="auto_submit", outcome="pending"
    )
    db.add(attempt)
    db.flush()

    workflow.record_submission(
        db, application, attempt, confirmation_number="SECRET-CONF-42", receipt={}
    )
    db.flush()

    for notification in db.execute(select(Notification)).scalars():
        assert "SECRET-CONF-42" not in notification.title
        assert "SECRET-CONF-42" not in notification.body
        assert "SECRET-CONF-42" not in str(notification.data)
    assert application.confirmation_number == "SECRET-CONF-42"


# ------------------------------------------------------------------ privacy
def test_the_export_carries_the_stored_files_not_just_their_rows(client, auth_headers, profile, db):
    """docs/COMPLIANCE.md promises "the stored files"; it used to ship metadata."""
    from base64 import b64decode

    body = b"Jane Candidate resume bytes"
    assert (
        client.post(
            "/api/v1/documents",
            headers=auth_headers,
            files={"file": ("cv.txt", body, "text/plain")},
            data={"kind": "resume_source"},
        ).status_code
        == 201
    )

    export = client.get("/api/v1/privacy/export", headers=auth_headers)
    assert export.status_code == 200
    payload = export.json()
    assert payload["stored_files"], payload.keys()
    assert b64decode(payload["stored_files"][0]["content"]) == body
    assert payload["stored_files_omitted"] == []


def test_the_digest_survives_a_match_with_no_explanation(
    client, auth_headers, profile, facts, job, db
):
    from app.models.job import JobMatch

    client.post("/api/v1/matches/rescore", headers=auth_headers)
    for match in db.execute(select(JobMatch)).scalars():
        match.explanation = ""
    db.flush()

    response = client.post("/api/v1/notifications/digest/send", headers=auth_headers)
    assert response.status_code == 200
