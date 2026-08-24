"""The companies roll-up: every employer that has posted, counted correctly."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import make_job

pytestmark = pytest.mark.integration


def test_companies_are_grouped_and_counted(client, auth_headers, profile, facts, db):
    db.add_all(
        [
            make_job(external_id="nw-1", company="Northwind Systems", title="Backend Engineer"),
            make_job(external_id="nw-2", company="Northwind Systems", title="Platform Engineer"),
            make_job(external_id="cx-1", company="Contoso Retail", title="Data Analyst"),
        ]
    )
    db.flush()

    body = client.get("/api/v1/companies", headers=auth_headers).json()
    by_name = {item["company"]: item for item in body["items"]}

    assert body["total"] == 2
    assert by_name["Northwind Systems"]["job_count"] == 2
    assert by_name["Contoso Retail"]["job_count"] == 1
    assert by_name["Northwind Systems"]["connectors"] == ["greenhouse"]
    assert by_name["Northwind Systems"]["countries"] == ["US"]


def test_cosmetic_name_variants_collapse_into_one_employer(client, auth_headers, profile, db):
    """'Acme, Inc.' and 'ACME Inc' are one company, not two."""
    db.add_all(
        [
            make_job(external_id="a-1", company="Acme, Inc.", title="Backend Engineer"),
            make_job(external_id="a-2", company="ACME Inc", title="Data Engineer"),
        ]
    )
    db.flush()

    body = client.get("/api/v1/companies", headers=auth_headers).json()
    assert body["total"] == 1
    assert body["items"][0]["job_count"] == 2


def test_a_duplicate_listing_is_not_counted_twice(client, auth_headers, profile, db):
    """The same role found on a second board must not inflate the count."""
    canonical = make_job(external_id="canon-1", company="Northwind Systems")
    db.add(canonical)
    db.flush()

    duplicate = make_job(external_id="dupe-1", company="Northwind Systems")
    duplicate.canonical_job_id = canonical.id
    db.add(duplicate)
    db.flush()

    body = client.get("/api/v1/companies", headers=auth_headers).json()
    assert body["total"] == 1
    assert body["items"][0]["job_count"] == 1


def test_scores_and_applications_are_rolled_up_per_company(
    client, auth_headers, profile, facts, db, job
):
    client.post("/api/v1/matches/rescore", headers=auth_headers)
    client.post("/api/v1/applications/draft", headers=auth_headers, json={"job_id": str(job.id)})

    body = client.get("/api/v1/companies", headers=auth_headers).json()
    entry = next(i for i in body["items"] if i["company"] == "Northwind Systems")

    assert entry["scored_job_count"] >= 1
    assert entry["best_score"] is not None and entry["best_score"] > 0
    assert entry["applied_count"] == 1


def test_search_and_country_filters(client, auth_headers, profile, db):
    db.add_all(
        [
            make_job(external_id="us-1", company="Northwind Systems", location_country="US"),
            make_job(external_id="gb-1", company="Fabrikam Ltd", location_country="GB"),
        ]
    )
    db.flush()

    searched = client.get("/api/v1/companies?q=fabrikam", headers=auth_headers).json()
    assert [i["company"] for i in searched["items"]] == ["Fabrikam Ltd"]

    filtered = client.get("/api/v1/companies?country=GB", headers=auth_headers).json()
    assert [i["company"] for i in filtered["items"]] == ["Fabrikam Ltd"]


def test_posted_within_filter_excludes_old_postings(client, auth_headers, profile, db):
    old = datetime.now(UTC) - timedelta(days=30)
    db.add_all(
        [
            make_job(external_id="fresh-1", company="Northwind Systems"),
            make_job(external_id="old-1", company="Ancient Corp", posted_at=old, first_seen_at=old),
        ]
    )
    db.flush()

    body = client.get("/api/v1/companies?posted_within_hours=48", headers=auth_headers).json()
    names = [i["company"] for i in body["items"]]
    assert "Northwind Systems" in names
    assert "Ancient Corp" not in names


@pytest.mark.parametrize("sort", ["jobs", "recent", "score", "name"])
def test_every_sort_mode_returns_a_stable_order(client, auth_headers, profile, db, sort):
    db.add_all(
        [
            make_job(external_id="s-1", company="Alpha Corp"),
            make_job(external_id="s-2", company="Beta Corp"),
            make_job(external_id="s-3", company="Beta Corp", title="Second Role"),
        ]
    )
    db.flush()

    body = client.get(f"/api/v1/companies?sort={sort}", headers=auth_headers).json()
    assert body["total"] == 2
    if sort == "jobs":
        assert body["items"][0]["company"] == "Beta Corp"
    if sort == "name":
        assert [i["company"] for i in body["items"]] == ["Alpha Corp", "Beta Corp"]


def test_pagination_reports_the_full_total(client, auth_headers, profile, db):
    for index in range(5):
        db.add(make_job(external_id=f"p-{index}", company=f"Company {index}"))
    db.flush()

    body = client.get("/api/v1/companies?limit=2&offset=0", headers=auth_headers).json()
    assert body["total"] == 5
    assert len(body["items"]) == 2


def test_companies_requires_authentication(client):
    assert client.get("/api/v1/companies").status_code == 401


def test_one_user_cannot_see_another_users_scores(client, auth_headers, profile, facts, db, job):
    """Job rows are shared; the score and application counts are not."""
    from app.core.security import Role, hash_password
    from app.models.user import User

    client.post("/api/v1/matches/rescore", headers=auth_headers)

    other = User(
        email="other@example.com",
        hashed_password=hash_password("An0ther-Str0ng-Pass!"),
        full_name="Other",
        role=Role.OWNER.value,
    )
    db.add(other)
    db.flush()
    token = client.post(
        "/api/v1/auth/login",
        json={"email": "other@example.com", "password": "An0ther-Str0ng-Pass!"},
    ).json()["access_token"]

    body = client.get("/api/v1/companies", headers={"Authorization": f"Bearer {token}"}).json()
    entry = next(i for i in body["items"] if i["company"] == "Northwind Systems")

    # They can see the employer exists, but none of the first user's scoring.
    assert entry["job_count"] >= 1
    assert entry["best_score"] is None
    assert entry["scored_job_count"] == 0
    assert entry["applied_count"] == 0
