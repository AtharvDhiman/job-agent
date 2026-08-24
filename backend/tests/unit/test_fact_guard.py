"""The truthfulness gate. If these pass, the agent cannot ship an invented claim."""

from __future__ import annotations

from app.services import fact_guard


def _index(profile, facts):
    return fact_guard.FactIndex(profile, facts)


def test_clean_text_from_verified_facts_passes(profile, facts):
    index = _index(profile, facts)
    text = (
        "I am a Senior Backend Engineer at Northwind Systems. "
        "I built a Python and PostgreSQL service used by internal teams."
    )
    report = fact_guard.check(text, index, target_company="Contoso")
    assert not report.blocked, report.as_dict()


def test_invented_employer_is_blocked(profile, facts):
    report = fact_guard.check(
        "I worked at Globex Corporation for three years.", _index(profile, facts)
    )
    assert report.blocked
    assert any(f.kind == "unverified_employer" for f in report.flags)


def test_unverified_certification_is_blocked(profile, facts):
    """The AWS certification fact exists but is NOT verified, so it cannot be used."""
    report = fact_guard.check("I am an AWS Certified Solutions Architect.", _index(profile, facts))
    assert report.blocked
    assert any(f.kind == "unverified_credential" for f in report.flags)


def test_invented_degree_is_blocked(profile, facts):
    report = fact_guard.check("I hold a PhD in distributed systems.", _index(profile, facts))
    assert report.blocked
    assert any(f.kind == "unverified_credential" for f in report.flags)


def test_verified_degree_passes(profile, facts):
    report = fact_guard.check("B.S. Computer Science, State University", _index(profile, facts))
    assert not report.blocked, report.as_dict()


def test_invented_metric_is_blocked(profile, facts):
    report = fact_guard.check(
        "I improved throughput by 45 percent and served 12000 users.", _index(profile, facts)
    )
    assert report.blocked
    assert any(f.kind == "unverified_metric" for f in report.flags)


def test_invented_link_is_blocked(profile, facts):
    report = fact_guard.check(
        "See my work at https://not-my-real-portfolio.example.com", _index(profile, facts)
    )
    assert report.blocked
    assert any(f.kind == "unverified_link" for f in report.flags)


def test_profile_link_is_allowed(profile, facts):
    report = fact_guard.check("My code is at https://github.com/testowner", _index(profile, facts))
    assert not report.blocked, report.as_dict()


def test_insufficient_facts_token_blocks(profile, facts):
    report = fact_guard.check("INSUFFICIENT_FACTS", _index(profile, facts))
    assert report.blocked
    assert report.flags[0].kind == "insufficient_facts"


def test_target_company_is_not_treated_as_an_employer_claim(profile, facts):
    report = fact_guard.check(
        "I am excited to apply at Contoso Retail.",
        _index(profile, facts),
        target_company="Contoso Retail",
    )
    assert not any(f.kind == "unverified_employer" for f in report.flags)


def test_answer_without_a_source_fact_is_flagged(profile, facts):
    report = fact_guard.check_answer(
        "Yes", _index(profile, facts), source_fact_id=None, question="Do you have a work permit?"
    )
    assert report.blocked
    assert any(f.kind == "unsourced_answer" for f in report.flags)


def test_unverified_facts_are_excluded_from_the_index(profile, facts):
    index = _index(profile, facts)
    assert not index.mentions("AWS Certified Solutions Architect")
    assert index.mentions("Northwind Systems")


def test_work_authorization_prose_is_never_trusted(profile, facts):
    report = fact_guard.check(
        "I am a citizen and require no visa sponsorship.", _index(profile, facts)
    )
    assert report.blocked
    assert any(f.kind == "unverified_work_authorization" for f in report.flags)
