"""Every known way a fabricated claim has tried to slip past the fact guard.

Each case below is a real bypass that existed at some point: a claim phrased so
that an earlier version of the guard did not recognise it as a claim at all.
They are kept as a matrix rather than prose tests because the failure mode is
uniform -- the guard must return blocked -- and because the list is the useful
artefact: it is the attack surface, written down.

A case moving from blocked to slipped is a regression that ships a lie.
"""

from __future__ import annotations

import pytest

from app.services import fact_guard

# (label, text, the flag kind that must catch it)
BYPASS_ATTEMPTS = [
    # --- employers named without an employment preposition --------------
    (
        "employer_without_preposition",
        "Acme Corp - Senior Engineer, leading the platform team.",
        "unverified_employer",
    ),
    ("employer_in_bullet", "- Globex Corporation, 2019-2021", "unverified_employer"),
    ("employer_lowercase", "I worked at globex corporation.", "unverified_employer"),
    ("employer_ex_prefix", "Ex-Google Staff Engineer, fintech specialist.", "unverified_employer"),
    # --- metrics that carry no percent sign -----------------------------
    ("metric_worded_tripled", "I tripled throughput on the checkout path.", "unverified_metric"),
    ("metric_worded_doubled", "I doubled conversion for the signup funnel.", "unverified_metric"),
    ("metric_unusual_unit", "I reduced latency by 40ms.", "unverified_metric"),
    ("metric_currency", "I saved $2M in cloud spend.", "unverified_metric"),
    ("metric_years_of_experience", "I have 10 years of experience.", "unverified_metric"),
    # --- qualifications phrased as sentences ----------------------------
    ("degree_without_keyword", "I studied Computer Science at Stanford.", None),
    ("degree_without_keyword_comma", "I studied Computer Science, Stanford University.", None),
    (
        "certification_as_sentence",
        "I completed the AWS Solutions Architect exam.",
        "unverified_credential",
    ),
    (
        "certification_as_sentence_2",
        "I passed the Certified Kubernetes Administrator exam.",
        "unverified_credential",
    ),
    # --- links that dodge the https:// prefix ---------------------------
    ("link_without_scheme", "See github.com/someoneelse for my code.", "unverified_link"),
    ("link_uppercase_scheme", "See HTTPS://EVIL.EXAMPLE for my code.", "unverified_link"),
    # --- dates written as prose -----------------------------------------
    ("date_since_year", "I have been building distributed systems since 2016.", None),
    ("date_span_in_words", "Over the past 8 years I have led platform teams.", None),
    # --- the three categories that may never be inferred at all ---------
    (
        "work_authorization",
        "I hold permanent residency in the United States.",
        "unverified_work_authorization",
    ),
    ("salary_history", "My current compensation is 190000 USD.", "unverified_salary_claim"),
    ("reference", "My manager Jane Roe can be reached for a reference.", "unverified_reference"),
]


@pytest.mark.parametrize(
    "label,text,expected_kind",
    BYPASS_ATTEMPTS,
    ids=[case[0] for case in BYPASS_ATTEMPTS],
)
def test_known_bypass_is_blocked(profile, facts, label, text, expected_kind):
    """The claim must be blocked, and where a specific rule owns it, by that rule."""
    index = fact_guard.FactIndex(profile, facts)
    report = fact_guard.check(text, index, target_company="Contoso")

    assert report.blocked, (
        f"{label!r} slipped past the fact guard. Text: {text!r}. "
        f"Flags raised: {[f.kind for f in report.flags]}"
    )
    if expected_kind is not None:
        kinds = {f.kind for f in report.flags if f.severity == fact_guard.SEVERITY_BLOCK}
        assert expected_kind in kinds, (
            f"{label!r} was blocked, but not by the rule that should own it. "
            f"Expected {expected_kind!r}, got {sorted(kinds)}"
        )


def test_the_matrix_covers_every_promise_category():
    """Guards against someone quietly deleting a whole category from the matrix."""
    labels = " ".join(case[0] for case in BYPASS_ATTEMPTS)
    for category in (
        "employer",
        "metric",
        "degree",
        "certification",
        "link",
        "date",
        "work_authorization",
        "salary",
        "reference",
    ):
        assert category in labels, f"no bypass case covers {category}"


def test_truthful_text_built_from_verified_facts_is_not_blocked(profile, facts):
    """The counterweight: a guard that blocks everything is useless.

    This is the text a correct generator produces from the fixture's verified
    facts. If tightening a rule above starts failing here, the rule is too broad.
    """
    index = fact_guard.FactIndex(profile, facts)
    report = fact_guard.check(
        "I am a Senior Backend Engineer at Northwind Systems. "
        "I built a Python and PostgreSQL service used by internal teams. "
        "My code is at https://github.com/testowner",
        index,
        target_company="Contoso",
    )
    assert not report.blocked, [f.as_dict() for f in report.flags]
