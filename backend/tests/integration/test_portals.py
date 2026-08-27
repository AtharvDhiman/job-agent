"""Per-portal readiness.

The point of this endpoint is that a user never has to guess why nothing is
happening on a platform. So the tests assert the *sentences*, not just the
enum: a portal that cannot act must say which switch is off.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.connectors import registry
from app.core.config import settings
from app.core.security import Role, hash_password
from app.models.job import JobSourceSubscription
from app.models.user import AgentSettings, User
from app.schemas.settings import AUTHORIZATION_ACKNOWLEDGEMENT

pytestmark = pytest.mark.integration


@pytest.fixture
def automation_on(monkeypatch):
    monkeypatch.setattr(settings, "automation_global_enabled", True)
    yield
    monkeypatch.setattr(settings, "automation_global_enabled", False)


def portals(client, auth_headers) -> dict[str, dict]:
    response = client.get("/api/v1/portals", headers=auth_headers)
    assert response.status_code == 200, response.text
    return {row["key"]: row for row in response.json()}


def authorize(client, auth_headers, platform="greenhouse", policy="auto_submit"):
    return client.post(
        "/api/v1/settings/authorizations",
        headers=auth_headers,
        json={
            "platform_key": platform,
            "policy": policy,
            "acknowledgement": AUTHORIZATION_ACKNOWLEDGEMENT,
        },
    )


def test_portals_requires_auth(client):
    assert client.get("/api/v1/portals").status_code == 401


def test_every_registered_connector_appears_exactly_once(client, auth_headers):
    response = client.get("/api/v1/portals", headers=auth_headers)
    assert response.status_code == 200
    keys = [row["key"] for row in response.json()]
    assert sorted(keys) == registry.keys()
    assert len(keys) == len(set(keys))


def test_results_are_ordered_ready_first(client, auth_headers):
    rank = {"ready": 0, "authorized": 1, "discovery_only": 2, "blocked": 3, "unsupported": 4}
    rows = client.get("/api/v1/portals", headers=auth_headers).json()
    ordered = [(rank[row["status"]], row["key"]) for row in rows]
    assert ordered == sorted(ordered)


@pytest.mark.parametrize("platform", ["linkedin", "indeed", "naukri"])
def test_prohibited_platforms_are_blocked_and_stay_blocked(client, auth_headers, platform):
    row = portals(client, auth_headers)[platform]
    assert row["status"] == "blocked"
    assert row["automation_permitted_for_submission"] is False
    assert row["browser_submission_supported"] is False
    assert any("terms" in blocker for blocker in row["blockers"])

    # Attempting to grant it is refused outright, and the report does not move.
    refused = authorize(client, auth_headers, platform=platform)
    assert refused.status_code == 403, refused.text
    assert "terms" in refused.json()["detail"]

    after = portals(client, auth_headers)[platform]
    assert after["status"] == "blocked"
    assert after["granted_policy"] is None
    assert after["automation_permitted_for_submission"] is False


def test_greenhouse_without_authorization_is_discovery_only(client, auth_headers):
    row = portals(client, auth_headers)["greenhouse"]
    assert row["status"] == "discovery_only"
    assert row["browser_submission_supported"] is True
    assert row["granted_policy"] is None
    assert any("Not authorized for auto-submit" in b for b in row["blockers"])


def test_authorized_but_paused_reports_the_pause(client, auth_headers, automation_on):
    assert authorize(client, auth_headers).status_code == 200
    client.post(
        "/api/v1/settings/pause",
        headers=auth_headers,
        json={"reason": "checking something"},
    )

    row = portals(client, auth_headers)["greenhouse"]
    assert row["status"] == "authorized"
    assert row["granted_policy"] == "auto_submit"
    assert any("Automation is paused" in b for b in row["blockers"])
    assert any("checking something" in b for b in row["blockers"])


def test_authorized_but_global_switch_off_is_not_ready(client, auth_headers):
    assert authorize(client, auth_headers).status_code == 200
    client.post("/api/v1/settings/resume", headers=auth_headers)

    row = portals(client, auth_headers)["greenhouse"]
    assert row["status"] == "authorized"
    assert any("switched off on the server" in b for b in row["blockers"])


def test_fully_switched_on_greenhouse_is_ready_with_no_blockers(
    client, auth_headers, db, user, automation_on
):
    db.add(
        JobSourceSubscription(
            user_id=user.id,
            connector_key="greenhouse",
            identifier="northwind",
            enabled=True,
            last_status="ok",
        )
    )
    db.flush()
    assert authorize(client, auth_headers).status_code == 200
    client.post("/api/v1/settings/resume", headers=auth_headers)

    row = portals(client, auth_headers)["greenhouse"]
    assert row["status"] == "ready"
    assert row["blockers"] == []
    assert row["granted_policy"] == "auto_submit"


def test_a_ready_portal_may_still_report_discovery_blockers(client, auth_headers, automation_on):
    """`status` answers "would a submit happen", not "is everything set up".

    An authorized portal with no source is genuinely ready to submit: paste a
    Greenhouse link into quick-add and it goes. What it cannot do is find that
    job on its own. Both facts are true at once, so the status stays `ready`
    while the blocker is still reported -- the two are appended on either side
    of the demotion check in portal_status.py on purpose.

    The UI relies on this: it reads `status == "ready"` to decide whether to
    head the blocker list "these only stop it finding jobs on its own" instead
    of "what is stopping a submit right now".
    """
    assert authorize(client, auth_headers).status_code == 200
    client.post("/api/v1/settings/resume", headers=auth_headers)

    row = portals(client, auth_headers)["greenhouse"]
    assert row["status"] == "ready"
    assert row["blockers"] == ["No source configured"]


def test_daily_limit_demotes_a_ready_portal(client, auth_headers, db, user, automation_on):
    db.add(
        JobSourceSubscription(
            user_id=user.id, connector_key="greenhouse", identifier="northwind", enabled=True
        )
    )
    db.flush()
    assert authorize(client, auth_headers).status_code == 200
    client.post("/api/v1/settings/resume", headers=auth_headers)
    client.patch("/api/v1/settings", headers=auth_headers, json={"daily_application_limit": 0})

    row = portals(client, auth_headers)["greenhouse"]
    assert row["status"] == "authorized"
    assert any("Daily limit of 0 reached" in b for b in row["blockers"])


def test_credentials_are_reported_for_a_partner_connector(client, auth_headers):
    row = portals(client, auth_headers)["adzuna"]
    assert row["status"] == "discovery_only"
    assert row["credentials_required"] == ["ADZUNA_APP_ID", "ADZUNA_APP_KEY"]
    assert row["credentials_present"] is False
    assert any("Missing credentials" in b for b in row["blockers"])


def test_manual_connector_is_unsupported(client, auth_headers):
    row = portals(client, auth_headers)["manual"]
    assert row["status"] == "unsupported"
    assert any("cannot discover jobs automatically" in b for b in row["blockers"])


def test_source_counters_are_per_user(client, auth_headers, db, user):
    ran_at = datetime(2026, 3, 4, 9, 30, tzinfo=UTC)
    other = User(
        email="stranger@example.com",
        hashed_password=hash_password("Another-Secret-Passw0rd!"),
        role=Role.OWNER.value,
    )
    db.add(other)
    db.flush()
    db.add(AgentSettings(user_id=other.id))
    db.add_all(
        [
            JobSourceSubscription(
                user_id=user.id,
                connector_key="greenhouse",
                identifier="northwind",
                enabled=True,
                jobs_seen=12,
                last_run_at=ran_at,
                last_status="ok",
            ),
            JobSourceSubscription(
                user_id=user.id,
                connector_key="greenhouse",
                identifier="acme",
                enabled=False,
                jobs_seen=5,
                last_run_at=datetime(2026, 3, 3, 9, 30, tzinfo=UTC),
                last_status="error",
            ),
            # Another user's board must never show up in this user's counters.
            JobSourceSubscription(
                user_id=other.id,
                connector_key="greenhouse",
                identifier="stranger-co",
                enabled=True,
                jobs_seen=999,
                last_run_at=datetime(2026, 5, 1, 9, 30, tzinfo=UTC),
                last_status="ok",
            ),
        ]
    )
    db.flush()

    row = portals(client, auth_headers)["greenhouse"]
    assert row["source_count"] == 2
    assert row["enabled_source_count"] == 1
    assert row["jobs_seen"] == 17
    assert row["last_run_at"].startswith("2026-03-04T09:30")
    assert row["error_count"] == 1
    assert any("failed on the last run" in b for b in row["blockers"])

    lever = portals(client, auth_headers)["lever"]
    assert lever["source_count"] == 0
    assert lever["jobs_seen"] == 0
    assert lever["last_run_at"] is None
    assert any("No source configured" in b for b in lever["blockers"])
