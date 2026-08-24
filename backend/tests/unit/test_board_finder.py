"""Board finder probes against mocked responses (no live network)."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.connectors import PoliteClient
from app.services.board_finder import find_boards, slug_candidates

GH = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
LEVER = "https://api.lever.co/v0/postings/{slug}?mode=json"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
SR = "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1"
WORKABLE = "https://apply.workable.com/api/v1/widget/accounts/{slug}"

ALL_TEMPLATES = (GH, LEVER, ASHBY, SR, WORKABLE)


@pytest.fixture
def http():
    with PoliteClient() as client:
        yield client


def mock_misses(slugs: list[str], *, hits: set[str] = frozenset()) -> None:
    """404 every probe URL except the ones a test mocks as hits itself."""
    for template in ALL_TEMPLATES:
        for slug in slugs:
            url = template.format(slug=slug)
            if url not in hits:
                respx.get(url).mock(return_value=httpx.Response(404))


# ------------------------------------------------------------ slug candidates
def test_slug_candidates_strips_suffixes_and_orders_variants():
    assert slug_candidates("Northwind Systems, Inc.") == [
        "northwindsystems",
        "northwind-systems",
        "northwind",
    ]


def test_slug_candidates_single_word_yields_one_candidate():
    assert slug_candidates("Stripe") == ["stripe"]


def test_slug_candidates_strips_punctuation():
    assert slug_candidates("Foo.Bar & Baz! LLC") == ["foobarbaz", "foo-bar-baz", "foo"]


def test_slug_candidates_empty_and_suffix_only_names():
    assert slug_candidates("") == []
    assert slug_candidates("Inc.") == []


# --------------------------------------------------------------------- probes
@respx.mock
def test_greenhouse_hit_stops_further_slugs_for_that_platform(http):
    slugs = ["northwindsystems", "northwind-systems"]
    gh_hit = respx.get(GH.format(slug=slugs[0])).mock(
        return_value=httpx.Response(200, json={"jobs": [{"id": 1}, {"id": 2}, {"id": 3}]})
    )
    gh_second = respx.get(GH.format(slug=slugs[1])).mock(
        return_value=httpx.Response(200, json={"jobs": []})
    )
    mock_misses(slugs, hits={GH.format(slug=s) for s in slugs})

    results = find_boards("Northwind Systems, Inc.", client=http)

    assert len(results) == 1
    board = results[0]
    assert board["connector_key"] == "greenhouse"
    assert board["identifier"] == "northwindsystems"
    assert board["probed_slug"] == "northwindsystems"
    assert board["job_count"] == 3
    assert board["url"] == "https://boards.greenhouse.io/northwindsystems"
    assert board["display_name"] == "Northwind Systems, Inc."
    assert gh_hit.called
    assert not gh_second.called


@respx.mock
def test_company_found_nowhere_returns_empty(http):
    mock_misses(["acme"])
    assert find_boards("Acme", client=http) == []


@respx.mock
def test_bot_wall_is_not_found_not_an_error(http):
    url = WORKABLE.format(slug="acme")
    mock_misses(["acme"], hits={url})
    respx.get(url).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html>Checking your browser before accessing this site</html>",
        )
    )
    # BlockedByPolicyError from the bot wall must be swallowed as "not found".
    assert find_boards("Acme", client=http) == []


@respx.mock
def test_company_on_two_platforms_returns_both(http):
    gh_url = GH.format(slug="acme")
    lever_url = LEVER.format(slug="acme")
    mock_misses(["acme"], hits={gh_url, lever_url})
    respx.get(gh_url).mock(return_value=httpx.Response(200, json={"jobs": [{"id": 1}]}))
    respx.get(lever_url).mock(return_value=httpx.Response(200, json=[{"id": "a"}, {"id": "b"}]))

    results = find_boards("Acme", client=http)

    by_key = {r["connector_key"]: r for r in results}
    assert set(by_key) == {"greenhouse", "lever"}
    assert by_key["greenhouse"]["job_count"] == 1
    assert by_key["lever"]["job_count"] == 2
    assert by_key["lever"]["url"] == "https://jobs.lever.co/acme"
