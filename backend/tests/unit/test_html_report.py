"""The report is a file the user opens from disk, which sets the bar for it.

Offline means no asset may live behind a URL, and `file://` means an unescaped
job title from a feed would run as script with nothing to contain it. Both are
pinned here, along with the empty-database case -- the state every new user sees
first, and the one that is easiest to ship broken.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest

from app.core.enums import MatchDecision
from app.models.job import JobMatch
from app.services.html_report import PORTAL_STATUS_NOTES, render_report
from app.services.pipeline import BUCKET_LABELS
from tests.conftest import make_job

XSS_TITLE = "<script>alert('pwn')</script> Senior Engineer"


def _add_match(db, user, *, score=90, decision=MatchDecision.SHORTLISTED.value, **job_kwargs):
    job = make_job(**job_kwargs)
    db.add(job)
    db.flush()
    match = JobMatch(
        user_id=user.id,
        job_id=job.id,
        score=score,
        decision=decision,
        matching_skills=["python"],
        missing_skills=[],
        risks=[],
        explanation="deterministic",
    )
    db.add(match)
    db.flush()
    return job, match


def test_renders_with_an_empty_database(db, user):
    html = render_report(db, user)

    assert html.startswith("<!doctype html>")
    assert html.rstrip().endswith("</html>")
    # An empty report has to say what to do, not just print zeroes.
    assert "Nothing scored yet" in html
    assert "Next step:" in html
    assert "No jobs have been scored yet" in html


def test_renders_without_agent_settings_and_writes_nothing(db, user):
    """A user with no settings row still gets a report, and stays without one."""
    from app.models.user import AgentSettings

    db.query(AgentSettings).delete()
    db.flush()

    html = render_report(db, user)

    assert "Auto-submit at" in html
    assert db.query(AgentSettings).count() == 0


def test_untrusted_job_title_is_escaped(db, user):
    _add_match(db, user, title=XSS_TITLE, company="<img src=x onerror=alert(1)>")

    html = render_report(db, user)

    assert XSS_TITLE not in html
    assert "<script>alert(" not in html
    assert "&lt;script&gt;alert(&#x27;pwn&#x27;)&lt;/script&gt;" in html
    # The payload survives as inert text, never as a tag the browser would parse.
    assert "<img" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    # Exactly one script element, ours, and it carries no feed data.
    assert html.count("<script>") == 1


def test_javascript_apply_url_is_not_linked(db, user):
    """html.escape says nothing about the scheme, so the scheme is checked."""
    _add_match(
        db,
        user,
        apply_url="javascript:alert(document.domain)",
        source_url="javascript:alert(1)",
    )

    html = render_report(db, user)

    assert "javascript:" not in html
    assert "No link" in html


def test_https_apply_url_is_linked(db, user):
    _add_match(db, user, apply_url="https://boards.greenhouse.io/acme/jobs/7")

    html = render_report(db, user)

    assert 'href="https://boards.greenhouse.io/acme/jobs/7"' in html
    assert 'rel="noopener noreferrer"' in html


def test_all_six_bucket_names_appear(db, user):
    html = render_report(db, user)

    assert len(BUCKET_LABELS) == 6
    for _key, label, _note in BUCKET_LABELS:
        assert label in html, label


def test_the_report_and_the_api_count_the_same_six_buckets(db, user, profile, facts, job):
    """One definition of "needs review", not one per front door.

    These numbers used to be computed twice -- once in the dashboard route and
    once here in fresh SQL -- with no test that the two agreed. They are now
    the same call, and this is what says so.
    """
    from datetime import UTC as _UTC

    from app.api.deps import agent_settings_or_default
    from app.api.v1.dashboard import dashboard as dashboard_view
    from app.services import pipeline

    agent_settings = agent_settings_or_default(db, user)
    view = dashboard_view(db, user, hours=24, agent_settings=agent_settings)
    since = datetime.now(_UTC) - timedelta(hours=24)
    direct = pipeline.buckets(db, user, agent_settings, since)

    assert set(direct) == {key for key, _label, _note in BUCKET_LABELS}
    for key, _label, _note in BUCKET_LABELS:
        assert view.buckets[key]["count"] == direct[key]["count"], key


def test_every_portal_status_has_a_sentence_explaining_what_it_costs(db, user):
    """A new status must arrive with its note, not render as a blank paragraph.

    PORTAL_STATUS_NOTES is hand-written prose keyed by portal_status's own
    vocabulary. Renaming a status there, or adding a sixth, would otherwise
    produce a portal card that says nothing at all, silently.
    """
    from app.services import portal_status

    assert set(PORTAL_STATUS_NOTES) == set(portal_status.STATUS_ORDER)


def test_document_is_self_contained(db, user, profile):
    _add_match(db, user, apply_url="https://boards.greenhouse.io/acme/jobs/7")

    html = render_report(db, user)

    # No external asset may be referenced: styles and scripts are inline, and
    # there is no image, font or iframe at all.
    assert "<link" not in html.lower()
    assert not re.search(r"<script[^>]+src\s*=", html, re.IGNORECASE)
    assert not re.search(r"""\bsrc\s*=\s*["']?https?:""", html, re.IGNORECASE)
    assert "@import" not in html
    assert not re.search(r"url\(\s*['\"]?https?:", html, re.IGNORECASE)
    assert "<iframe" not in html.lower()
    assert "<style>" in html and "<script>" in html

    # The only absolute URLs left are anchors the user clicks on purpose.
    for href in re.findall(r'href="([^"]*)"', html):
        assert href.startswith("https://boards.greenhouse.io/"), href


def test_scored_jobs_table_carries_sort_and_filter_hooks(db, user):
    _add_match(db, user, score=91, title="Staff Backend Engineer")
    _add_match(
        db,
        user,
        score=40,
        decision=MatchDecision.BELOW_THRESHOLD.value,
        title="Junior Analyst",
        company="Other Corp",
    )

    html = render_report(db, user)

    assert 'id="jobs"' in html
    assert 'id="f-text"' in html and 'id="f-decision"' in html and 'id="f-score"' in html
    assert "data-sortable" in html
    assert 'data-score="91"' in html
    assert 'data-decision="below_threshold"' in html
    assert "Staff Backend Engineer" in html
    assert "Junior Analyst" in html
    # The search index is lowercased so the inline filter can do a plain compare.
    assert 'data-search="staff backend engineer' in html


def test_rejection_reasons_are_broken_down_with_labels(db, user):
    _add_match(db, user, score=20, decision=MatchDecision.EXCLUDED_COMPANY.value)
    _add_match(
        db,
        user,
        score=10,
        decision=MatchDecision.STALE_POSTING.value,
        title="Old Role",
        company="Stale Inc",
    )

    html = render_report(db, user)

    assert "Rejection reasons" in html
    assert "Company on your avoid list" in html
    assert "Older than your posting window" in html
    # The raw enum value stays visible: the label is for humans, the value is
    # what the API filters on.
    assert "excluded_company" in html


def test_portal_readiness_says_why_a_platform_is_review_only(db, user):
    html = render_report(db, user)

    assert "Portal readiness" in html
    # Prohibited platforms must be reported as permanently review-only.
    assert "prohibits automated applying" in html
    assert "will never submit here" in html


@pytest.mark.parametrize("hours", [1, 24, 720])
def test_window_is_reported_in_the_header(db, user, hours):
    html = render_report(db, user, hours=hours)

    assert f"activity window {hours}h" in html


def test_automation_state_is_in_the_header(db, user):
    """AUTOMATION_GLOBAL_ENABLED is false in tests, so the report must say so."""
    html = render_report(db, user)

    assert "Automation" in html
    assert "Off (server kill-switch)" in html
    assert "Applications today" in html
    assert "0 / 10" in html


def test_dark_mode_colours_are_defined(db, user):
    html = render_report(db, user)

    assert ":root {" in html
    assert "@media (prefers-color-scheme: dark)" in html
    assert "color-scheme: light dark" in html


def test_job_limit_caps_the_table_and_says_so(db, user):
    now = datetime.now(UTC)
    for index in range(3):
        _add_match(
            db,
            user,
            score=90 - index,
            title=f"Role {index}",
            posted_at=now - timedelta(hours=index),
        )

    html = render_report(db, user, job_limit=2)

    assert "Showing the top 2 of 3 scored jobs." in html
    assert "Role 2" not in html
