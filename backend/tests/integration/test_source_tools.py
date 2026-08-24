"""Source-tools endpoints: the board finder and the curated catalog."""

from __future__ import annotations

import httpx
import pytest
import respx

pytestmark = pytest.mark.integration

GH = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
LEVER = "https://api.lever.co/v0/postings/{slug}?mode=json"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
SR = "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1"
WORKABLE = "https://apply.workable.com/api/v1/widget/accounts/{slug}"

PROBE_HOSTS = {
    "boards-api.greenhouse.io",
    "api.lever.co",
    "api.ashbyhq.com",
    "api.smartrecruiters.com",
    "apply.workable.com",
}


def mock_misses(slugs: list[str], *, hits: set[str] = frozenset()) -> None:
    for template in (GH, LEVER, ASHBY, SR, WORKABLE):
        for slug in slugs:
            url = template.format(slug=slug)
            if url not in hits:
                respx.get(url).mock(return_value=httpx.Response(404))


def test_source_tools_require_auth(client):
    assert client.post("/api/v1/sources/find", json={"company": "Acme"}).status_code == 401
    assert client.get("/api/v1/sources/catalog").status_code == 401


@respx.mock
def test_find_then_add_then_already_added(client, auth_headers):
    gh_url = GH.format(slug="northwind")
    mock_misses(["northwind"], hits={gh_url})
    respx.get(gh_url).mock(return_value=httpx.Response(200, json={"jobs": [{"id": 1}, {"id": 2}]}))

    first = client.post("/api/v1/sources/find", json={"company": "Northwind"}, headers=auth_headers)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["company"] == "Northwind"
    [candidate] = body["candidates"]
    assert candidate["connector_key"] == "greenhouse"
    assert candidate["identifier"] == "northwind"
    assert candidate["job_count"] == 2
    assert candidate["already_added"] is False

    add = client.post(
        "/api/v1/sources",
        headers=auth_headers,
        json={
            "connector_key": candidate["connector_key"],
            "identifier": candidate["identifier"],
            "display_name": candidate["display_name"],
        },
    )
    assert add.status_code == 201, add.text

    second = client.post(
        "/api/v1/sources/find", json={"company": "Northwind"}, headers=auth_headers
    )
    assert second.status_code == 200
    assert second.json()["candidates"][0]["already_added"] is True


@respx.mock
def test_find_never_leaves_the_five_probe_hosts(client, auth_headers):
    # Mock ONLY the five documented hosts. respx is in assert_all_mocked mode,
    # so a request to any other host raises and fails the test.
    for host in PROBE_HOSTS:
        respx.route(host=host).mock(return_value=httpx.Response(404))

    resp = client.post(
        "/api/v1/sources/find",
        json={"company": "Northwind Systems, Inc."},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["candidates"] == []
    assert respx.calls, "the finder should have probed the documented hosts"
    assert {call.request.url.host for call in respx.calls} <= PROBE_HOSTS


def test_catalog_availability_and_already_added(client, auth_headers):
    resp = client.get("/api/v1/sources/catalog", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    entries = resp.json()

    adzuna = [e for e in entries if e["connector_key"] == "adzuna"]
    assert len(adzuna) == 1
    # Test environment holds no Adzuna credentials, so the entry is visible
    # but not addable.
    assert adzuna[0]["available"] is False
    assert adzuna[0]["requires_credentials"] == ["ADZUNA_APP_ID", "ADZUNA_APP_KEY"]

    rss = [e for e in entries if e["connector_key"] == "rss"]
    assert len(rss) == 3
    assert all(e["available"] is True for e in rss)
    assert all(e["compliance_note"] for e in entries)
    assert all(e["already_added"] is False for e in entries)

    target = rss[0]
    add = client.post(
        "/api/v1/sources",
        headers=auth_headers,
        json={
            "connector_key": target["connector_key"],
            "identifier": target["identifier"],
            "display_name": target["display_name"],
        },
    )
    assert add.status_code == 201, add.text

    after = client.get("/api/v1/sources/catalog", headers=auth_headers).json()
    flags = {e["identifier"]: e["already_added"] for e in after}
    assert flags[target["identifier"]] is True
    assert sum(flags.values()) == 1
