"""A pasted URL is a claim about origin. These pin what we do with that claim."""

from __future__ import annotations

import pytest

from app.core.enums import ComplianceTier, SubmissionPolicy
from app.services.source_attribution import attribute, connector_key_for_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.naukri.com/job-listings-data-analyst-acme-1", "naukri"),
        ("https://naukri.com/jobs", "naukri"),
        ("https://www.naukrigulf.com/job-x", "naukri"),
        ("https://www.linkedin.com/jobs/view/12345", "linkedin"),
        ("https://uk.indeed.com/viewjob?jk=abc", "indeed"),
        ("https://boards.greenhouse.io/acme/jobs/1", "greenhouse"),
        ("https://job-boards.greenhouse.io/acme/jobs/1", "greenhouse"),
        ("https://jobs.lever.co/acme/1", "lever"),
        ("https://jobs.ashbyhq.com/acme/1", "ashby"),
        ("https://apply.workable.com/acme/j/1", "workable"),
        ("https://careers.smartrecruiters.com/acme/1", "smartrecruiters"),
    ],
)
def test_known_hosts_are_attributed(url, expected):
    assert connector_key_for_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://careers.example.com/roles/1",
        "https://example.com/naukri-integration-engineer",  # the word, not the host
        "https://notnaukri.com/jobs/1",
        "https://notlinkedin.com/jobs/1",
        "",
        "not a url at all",
    ],
)
def test_unknown_hosts_fall_back_to_manual(url):
    """Unrecognised is the safe answer: most jobs live on a careers page."""
    assert connector_key_for_url(url) == "manual"


@pytest.mark.parametrize(
    "url",
    [
        "https://greenhouse.io.evil.com/x",
        "https://naukri.com.evil.com/x",
        "https://evil.com/?u=https://www.naukri.com/job-1",
    ],
)
def test_a_lookalike_host_is_not_attributed(url):
    """Suffix match on the host, never a substring match on the URL.

    Getting this wrong in the permissive direction would hand a prohibited
    platform's policy to an attacker-chosen domain; getting it wrong in the
    other direction only costs a job the label `manual`.
    """
    assert connector_key_for_url(url) == "manual"


def test_a_naukri_url_inherits_the_prohibited_policy():
    key, tier, submission_policy = attribute("https://www.naukri.com/job-listings-x-1")
    assert key == "naukri"
    assert tier == ComplianceTier.MANUAL_ONLY.value
    assert submission_policy == SubmissionPolicy.PROHIBITED.value


def test_an_ats_url_keeps_its_review_required_default():
    """Attribution must not promote a job straight to auto-submit.

    Recognising a Greenhouse link says the FORM is one we can drive. Whether we
    may drive it is a separate grant, so the policy stays REVIEW_REQUIRED here.
    """
    key, tier, submission_policy = attribute("https://boards.greenhouse.io/acme/jobs/1")
    assert key == "greenhouse"
    assert tier == ComplianceTier.PUBLIC_JOB_API.value
    assert submission_policy == SubmissionPolicy.REVIEW_REQUIRED.value


def test_an_unknown_url_is_manual_and_reviewable():
    assert attribute("https://careers.example.com/x") == (
        "manual",
        ComplianceTier.MANUAL_ONLY.value,
        SubmissionPolicy.REVIEW_REQUIRED.value,
    )


def test_every_attributed_key_is_a_real_connector():
    """A typo here would 500 quick-add rather than mislabel a job."""
    from app.connectors import registry

    known = set(registry.keys())
    for url in (
        "https://www.naukri.com/x",
        "https://www.linkedin.com/jobs/view/1",
        "https://uk.indeed.com/viewjob?jk=a",
        "https://boards.greenhouse.io/a/jobs/1",
        "https://jobs.lever.co/a/1",
        "https://jobs.ashbyhq.com/a/1",
        "https://apply.workable.com/a/j/1",
        "https://careers.smartrecruiters.com/a/1",
    ):
        assert connector_key_for_url(url) in known, url
