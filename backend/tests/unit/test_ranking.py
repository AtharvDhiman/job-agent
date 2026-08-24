"""Scoring is deterministic, explainable, and hard filters are absolute."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.enums import MatchDecision, Seniority, WorkArrangement
from app.services.ranking import WEIGHTS, SemanticIndex, evaluate_hard_filters, score_job
from tests.conftest import make_job


def test_weights_sum_to_one_hundred():
    assert sum(WEIGHTS.values()) == 100


def test_strong_match_scores_high(profile):
    breakdown = score_job(make_job(), profile)
    assert breakdown.decision == MatchDecision.SHORTLISTED.value
    assert breakdown.score >= 75, breakdown.explanation
    assert "python" in breakdown.matching_skills
    assert breakdown.explanation.startswith("Score ")


def test_score_is_deterministic(profile):
    job = make_job()
    now = datetime.now(UTC)
    first = score_job(job, profile, now=now)
    second = score_job(job, profile, now=now)
    assert first.score == second.score
    assert first.components == second.components


def test_components_sum_to_the_score(profile):
    breakdown = score_job(make_job(), profile)
    assert round(sum(breakdown.components.values())) == breakdown.score


def test_avoided_company_is_rejected_not_ranked(profile):
    breakdown = score_job(make_job(company="Blocked Corp"), profile)
    assert breakdown.score == 0
    assert breakdown.decision == MatchDecision.EXCLUDED_COMPANY.value
    assert any("avoid list" in f for f in breakdown.hard_filter_failures)


def test_excluded_keyword_is_rejected(profile):
    breakdown = score_job(
        make_job(description_text="This is an unpaid internship opportunity."), profile
    )
    assert breakdown.score == 0
    assert breakdown.decision == MatchDecision.EXCLUDED_KEYWORD.value


def test_stale_posting_is_rejected(profile):
    old = datetime.now(UTC) - timedelta(hours=100)
    breakdown = score_job(make_job(posted_at=old, first_seen_at=old), profile, max_age_hours=48)
    assert breakdown.score == 0
    assert breakdown.decision == MatchDecision.STALE_POSTING.value


def test_expired_deadline_is_rejected(profile):
    breakdown = score_job(make_job(deadline_at=datetime.now(UTC) - timedelta(days=1)), profile)
    assert breakdown.score == 0
    assert any("deadline" in f.lower() for f in breakdown.hard_filter_failures)


def test_wrong_work_arrangement_is_rejected(profile):
    breakdown = score_job(make_job(work_arrangement=WorkArrangement.ONSITE.value), profile)
    assert breakdown.score == 0
    assert any("arrangement" in f for f in breakdown.hard_filter_failures)


def test_salary_below_minimum_is_rejected(profile):
    breakdown = score_job(make_job(salary_min=60000, salary_max=90000), profile)
    assert breakdown.score == 0
    assert any("below your" in f for f in breakdown.hard_filter_failures)


def test_sponsorship_conflict_is_rejected(profile):
    profile.requires_sponsorship = True
    breakdown = score_job(make_job(visa_sponsorship_mentioned=False), profile)
    assert breakdown.score == 0
    assert any("sponsor" in f for f in breakdown.hard_filter_failures)


def test_seniority_gap_is_penalised_and_explained(profile):
    breakdown = score_job(
        make_job(title="Director of Engineering", seniority=Seniority.DIRECTOR.value), profile
    )
    assert breakdown.components["seniority"] < WEIGHTS["seniority"] / 2
    assert any("levels above" in r for r in breakdown.risks)


def test_fresher_posting_outranks_older_one(profile):
    now = datetime.now(UTC)
    fresh = score_job(make_job(posted_at=now - timedelta(hours=2)), profile, now=now)
    older = score_job(make_job(posted_at=now - timedelta(hours=40)), profile, now=now)
    assert fresh.components["freshness"] > older.components["freshness"]
    assert fresh.score >= older.score


def test_direct_employer_is_preferred(profile):
    direct = score_job(make_job(is_direct_employer=True), profile)
    indirect = score_job(make_job(is_direct_employer=False), profile)
    assert direct.components["direct_employer"] > indirect.components["direct_employer"]
    assert any("aggregator" in r for r in indirect.risks)


def test_missing_skills_are_listed(profile):
    breakdown = score_job(
        make_job(extracted_skills=["python", "rust", "scala", "terraform"]), profile
    )
    assert "python" in breakdown.matching_skills
    assert {"rust", "scala", "terraform"} <= set(breakdown.missing_skills)


def test_explanation_covers_every_required_dimension(profile):
    text = score_job(make_job(), profile).explanation.lower()
    for needle in (
        "matching skills",
        "missing skills",
        "seniority",
        "location",
        "salary",
        "work authorization",
    ):
        assert needle in text, needle


def test_semantic_index_discounts_boilerplate():
    """Terms in every posting carry less weight than the ones that distinguish it."""
    boilerplate = "we are an equal opportunity employer and value diversity strongly"
    skills = ["python", "rust", "scala", "kotlin", "elixir", "haskell", "clojure", "erlang"]
    corpus = [f"{boilerplate} {skill} engineering role" for skill in skills]
    index = SemanticIndex(corpus)

    target = corpus[0]
    distinguishing = index.similarity("python", target)
    generic = index.similarity(boilerplate, target)
    assert distinguishing > generic, (distinguishing, generic)


def test_hard_filters_return_empty_for_a_good_job(profile):
    assert evaluate_hard_filters(make_job(), profile, max_age_hours=48) == []
