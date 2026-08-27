"""The automation gate. Default-deny, and every deny states its reason."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.enums import ReviewReason, SubmissionPolicy
from app.models.user import AgentSettings, PlatformAuthorization
from app.services import policy
from tests.conftest import make_job


def settings_row(**kw) -> AgentSettings:
    row = AgentSettings(
        automation_enabled=kw.pop("automation_enabled", True),
        paused_reason=kw.pop("paused_reason", ""),
        auto_submit_min_score=kw.pop("auto_submit_min_score", 85),
        daily_application_limit=kw.pop("daily_application_limit", 10),
        job_max_age_hours=48,
        shortlist_min_score=60,
    )
    return row


def grant(platform: str, policy_value: SubmissionPolicy) -> PlatformAuthorization:
    return PlatformAuthorization(
        platform_key=platform,
        policy=policy_value.value,
        granted_at=datetime.now(UTC),
        revoked_at=None,
    )


def decide(**overrides):
    kwargs = dict(
        job=make_job(),
        connector_policy=SubmissionPolicy.REVIEW_REQUIRED.value,
        authorization=grant("greenhouse", SubmissionPolicy.AUTO_SUBMIT),
        agent_settings=settings_row(),
        score=95,
        global_enabled=True,
        applications_today=0,
    )
    kwargs.update(overrides)
    return policy.decide(**kwargs)


def test_fully_authorized_high_score_may_auto_submit():
    decision = decide()
    assert decision.may_submit is True
    assert decision.policy == SubmissionPolicy.AUTO_SUBMIT.value
    assert decision.rationale


def test_without_authorization_it_goes_to_review():
    decision = decide(authorization=None)
    assert decision.may_submit is False
    assert decision.may_autofill is False
    assert ReviewReason.PLATFORM_NOT_AUTHORIZED.value in decision.review_reasons


def test_revoked_authorization_is_ignored():
    revoked = grant("greenhouse", SubmissionPolicy.AUTO_SUBMIT)
    revoked.revoked_at = datetime.now(UTC)
    decision = decide(authorization=revoked)
    assert decision.may_submit is False
    assert ReviewReason.PLATFORM_NOT_AUTHORIZED.value in decision.review_reasons


def test_score_below_threshold_goes_to_review():
    decision = decide(score=84)
    assert decision.may_submit is False
    assert ReviewReason.BELOW_AUTO_SUBMIT_THRESHOLD.value in decision.review_reasons
    # Still allowed to prefill the form for a human to finish.
    assert decision.may_autofill is True


def test_global_kill_switch_stops_everything():
    decision = decide(global_enabled=False)
    assert decision.may_submit is False
    assert decision.may_autofill is False
    assert ReviewReason.AUTOMATION_DISABLED.value in decision.review_reasons


def test_user_pause_stops_everything():
    decision = decide(
        agent_settings=settings_row(automation_enabled=False, paused_reason="on holiday")
    )
    assert decision.may_submit is False
    assert decision.may_autofill is False
    assert any("on holiday" in r for r in decision.rationale)


def test_daily_limit_blocks_submission():
    decision = decide(applications_today=10)
    assert decision.may_submit is False
    assert ReviewReason.DAILY_LIMIT_REACHED.value in decision.review_reasons


def test_fact_guard_block_prevents_autofill_and_submission():
    decision = decide(fact_guard_blocked=True)
    assert decision.may_submit is False
    assert decision.may_autofill is False
    assert ReviewReason.FACT_GUARD_FLAGGED.value in decision.review_reasons


def test_unanswerable_question_prevents_autofill():
    decision = decide(blocking_questions=2)
    assert decision.may_submit is False
    assert decision.may_autofill is False
    assert ReviewReason.UNANSWERABLE_QUESTION.value in decision.review_reasons


def test_validation_errors_prevent_submission():
    decision = decide(validation_errors=1)
    assert decision.may_submit is False
    assert ReviewReason.VALIDATION_FAILED.value in decision.review_reasons


@pytest.mark.parametrize("platform", ["linkedin", "indeed", "naukri"])
def test_prohibited_platforms_can_never_be_automated(platform):
    """Even with a full grant, a perfect score and automation on."""
    decision = decide(
        job=make_job(connector_key=platform),
        authorization=grant(platform, SubmissionPolicy.AUTO_SUBMIT),
        score=100,
    )
    assert decision.policy == SubmissionPolicy.PROHIBITED.value
    assert decision.may_submit is False
    assert decision.may_autofill is False
    assert ReviewReason.PLATFORM_PROHIBITS_AUTOMATION.value in decision.review_reasons


def test_assisted_autofill_never_clicks_submit():
    decision = decide(authorization=grant("greenhouse", SubmissionPolicy.ASSISTED_AUTOFILL))
    assert decision.may_autofill is True
    assert decision.may_submit is False
    assert decision.policy == SubmissionPolicy.ASSISTED_AUTOFILL.value


def test_connector_level_prohibition_is_respected_even_for_other_platforms():
    decision = decide(
        job=make_job(connector_key="somewhere_new"),
        connector_policy=SubmissionPolicy.PROHIBITED.value,
    )
    assert decision.policy == SubmissionPolicy.PROHIBITED.value
    assert decision.may_submit is False


# --------------------------------------------------------------------------
# The job row is a snapshot; the registry is the current truth.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("platform", ["linkedin", "indeed", "naukri"])
def test_a_stale_job_row_cannot_unprohibit_a_platform(platform):
    """A row ingested before the connector was pinned to PROHIBITED must lose."""
    decision = decide(
        job=make_job(connector_key=platform),
        connector_policy=SubmissionPolicy.REVIEW_REQUIRED.value,
        authorization=grant(platform, SubmissionPolicy.AUTO_SUBMIT),
    )
    assert decision.policy == SubmissionPolicy.PROHIBITED.value
    assert decision.may_autofill is False


def test_an_unregistered_connector_is_treated_as_prohibited():
    """No connector to check the platform's terms against means no automation."""
    decision = decide(
        job=make_job(connector_key="mystery_board"),
        connector_policy=SubmissionPolicy.REVIEW_REQUIRED.value,
        authorization=grant("mystery_board", SubmissionPolicy.AUTO_SUBMIT),
    )
    assert decision.policy == SubmissionPolicy.PROHIBITED.value
    assert decision.may_submit is False


@pytest.mark.parametrize("spelling", ["LinkedIn", " linkedin ", "INDEED"])
def test_prohibited_platform_keys_are_matched_case_insensitively(spelling):
    decision = decide(job=make_job(connector_key=spelling), score=100)
    assert decision.policy == SubmissionPolicy.PROHIBITED.value
    assert policy.is_hard_prohibited(spelling) is True


# --------------------------------------------------------------------------
# Discovery is not permission to fill a form.
#
# These connectors are registered, allowed to read jobs, and not prohibited --
# they simply have no browser workflow. They must land in review, and no grant,
# score or approval may talk them out of it.
# --------------------------------------------------------------------------
DISCOVERY_ONLY = ["adzuna", "careers_page", "manual", "rss"]


@pytest.mark.parametrize("platform", DISCOVERY_ONLY)
def test_a_discovery_only_platform_is_never_auto_submitted(platform):
    decision = decide(
        job=make_job(connector_key=platform),
        authorization=grant(platform, SubmissionPolicy.AUTO_SUBMIT),
        score=100,
    )
    assert decision.policy == SubmissionPolicy.REVIEW_REQUIRED.value
    assert decision.may_submit is False
    assert decision.may_autofill is False
    assert decision.review_reasons == [ReviewReason.UNSUPPORTED_PLATFORM.value]
    assert any("discover" in line for line in decision.rationale)


@pytest.mark.parametrize("platform", DISCOVERY_ONLY)
def test_an_approval_cannot_unlock_a_discovery_only_platform(platform):
    """The review task is the whole point: you apply on the site yourself."""
    decision = decide(
        job=make_job(connector_key=platform),
        authorization=grant(platform, SubmissionPolicy.AUTO_SUBMIT),
        score=100,
    )
    assert policy.may_hand_out(decision, approved_by_human=True) is False
    assert policy.may_hand_out(decision, approved_by_human=False) is False


@pytest.mark.parametrize(
    "platform", ["greenhouse", "lever", "ashby", "workable", "smartrecruiters"]
)
def test_every_supported_platform_can_reach_auto_submit_when_fully_authorized(platform):
    """The other half of the contract: the allow-list is not empty in practice."""
    decision = decide(
        job=make_job(connector_key=platform),
        authorization=grant(platform, SubmissionPolicy.AUTO_SUBMIT),
        score=95,
    )
    assert decision.policy == SubmissionPolicy.AUTO_SUBMIT.value
    assert decision.may_submit is True


def test_unsupported_platform_is_checked_before_authorization():
    """An ungranted, unsupported platform reports the durable reason, not the fixable one.

    'Grant automation in Settings' would be false advice here: there is nothing
    to grant, and the grant endpoint refuses it.
    """
    decision = decide(job=make_job(connector_key="rss"), authorization=None)
    assert decision.review_reasons == [ReviewReason.UNSUPPORTED_PLATFORM.value]
    assert ReviewReason.PLATFORM_NOT_AUTHORIZED.value not in decision.review_reasons


def test_supports_browser_submission_is_false_for_an_unregistered_key():
    assert policy.registered_connector_supports_browser_submission("mystery_board") is False
    assert policy.registered_connector_supports_browser_submission("") is False


@pytest.mark.parametrize("spelling", ["GreenHouse", " greenhouse ", "SMARTRECRUITERS"])
def test_browser_support_is_matched_case_insensitively(spelling):
    assert policy.registered_connector_supports_browser_submission(spelling) is True


# --------------------------------------------------------------------------
# What a human approval may unlock at hand-out time.
# --------------------------------------------------------------------------
def test_may_hand_out_never_lifts_a_prohibited_platform():
    decision = decide(job=make_job(connector_key="linkedin"), score=100)
    assert policy.may_hand_out(decision, approved_by_human=True) is False
    assert policy.may_hand_out(decision, approved_by_human=False) is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"authorization": None},  # never granted
        {"global_enabled": False},  # kill-switch
        {"agent_settings": settings_row(automation_enabled=False)},  # user pause
        {"fact_guard_blocked": True},  # untrustworthy text
        {"blocking_questions": 1},  # cannot answer truthfully
    ],
)
def test_may_hand_out_is_not_unlocked_by_an_approval(overrides):
    decision = decide(**overrides)
    assert policy.may_hand_out(decision, approved_by_human=True) is False


def test_may_hand_out_allows_an_approved_low_scorer_to_be_filled():
    decision = decide(score=10)
    assert decision.may_submit is False
    assert policy.may_hand_out(decision, approved_by_human=True) is True


def test_every_review_reason_is_unliftable_unless_listed():
    """The allow-list is the contract: a new ReviewReason blocks by default."""
    for reason in ReviewReason:
        decision = policy.PolicyDecision(
            policy=SubmissionPolicy.REVIEW_REQUIRED.value,
            may_autofill=False,
            review_reasons=[reason.value],
        )
        expected = reason.value in policy.HUMAN_APPROVAL_MAY_LIFT
        assert policy.may_hand_out(decision, approved_by_human=True) is expected, reason
