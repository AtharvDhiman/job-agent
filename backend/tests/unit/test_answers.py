"""Screening answers: sourced or escalated. There is no guessing branch."""

from __future__ import annotations

from app.core.enums import QuestionType
from app.services.answers import EEO_DEFAULT, Question, answer_question, blocking


def q(text, **kw) -> Question:
    return Question(external_id=kw.pop("external_id", text[:40]), text=text, **kw)


def test_name_and_email_come_from_the_profile(profile, facts):
    assert answer_question(q("Full name"), profile, facts).value == "Test Owner"
    assert answer_question(q("Email address"), profile, facts).value == "owner@example.com"


def test_sponsorship_uses_the_explicit_field(profile, facts):
    answer = answer_question(
        q(
            "Will you now or in the future require visa sponsorship?",
            type=QuestionType.BOOLEAN.value,
            options=["Yes", "No"],
            required=True,
        ),
        profile,
        facts,
    )
    assert answer.needs_human is False
    assert answer.value == "No"
    assert answer.source_field == "profile.requires_sponsorship"


def test_unknown_sponsorship_escalates_instead_of_guessing(profile, facts):
    profile.requires_sponsorship = None
    answer = answer_question(q("Do you require sponsorship?", required=True), profile, facts)
    assert answer.needs_human is True
    assert answer.value is None


def test_work_authorization_without_a_record_escalates(profile, facts):
    profile.work_authorization = None
    facts = [f for f in facts if f.category != "work_authorization"]
    answer = answer_question(
        q("Are you legally authorized to work in the job location?", required=True),
        profile,
        facts,
    )
    assert answer.needs_human is True
    assert "never be guessed" in answer.reason


def test_salary_history_is_never_answered(profile, facts):
    answer = answer_question(q("What is your current salary?"), profile, facts)
    assert answer.needs_human is True
    assert "never auto-filled" in answer.reason


def test_salary_expectation_uses_your_stated_minimum(profile, facts):
    answer = answer_question(q("What is your expected salary?"), profile, facts)
    assert answer.needs_human is False
    assert "140000" in answer.value


def test_eeo_defaults_to_prefer_not_to_say(profile, facts):
    answer = answer_question(
        q(
            "What is your gender?",
            type=QuestionType.SINGLE_SELECT.value,
            options=["Male", "Female", "I prefer not to say"],
        ),
        profile,
        facts,
    )
    assert answer.value == "I prefer not to say"
    assert "never inferred" in answer.reason


def test_eeo_falls_back_to_the_default_string(profile, facts):
    answer = answer_question(q("Are you a protected veteran?"), profile, facts)
    assert answer.value == EEO_DEFAULT


def test_long_free_text_is_always_escalated(profile, facts):
    answer = answer_question(
        q(
            "Describe a difficult project you led.",
            type=QuestionType.LONG_TEXT.value,
            required=True,
        ),
        profile,
        facts,
    )
    assert answer.needs_human is True
    assert "Free-text" in answer.reason


def test_unrecognised_question_is_escalated(profile, facts):
    answer = answer_question(q("What is your favourite kind of sandwich?"), profile, facts)
    assert answer.needs_human is True
    assert "Unrecognised" in answer.reason


def test_missing_linkedin_is_not_invented(profile, facts):
    profile.linkedin_url = ""
    answer = answer_question(q("LinkedIn profile URL"), profile, facts)
    assert answer.needs_human is True
    assert "will not invent" in answer.reason


def test_years_of_experience_is_not_estimated(profile, facts):
    profile.years_experience = None
    answer = answer_question(q("How many years of experience do you have?"), profile, facts)
    assert answer.needs_human is True
    assert "will not be estimated" in answer.reason


def test_references_are_never_shared_automatically(profile, facts):
    answer = answer_question(q("Please list your references"), profile, facts)
    assert answer.needs_human is True


def test_blocking_only_returns_required_unanswered(profile, facts):
    answers = [
        answer_question(q("What is your favourite colour?", required=False), profile, facts),
        answer_question(
            q("Describe your ideal team", type=QuestionType.LONG_TEXT.value, required=True),
            profile,
            facts,
        ),
    ]
    assert len(blocking(answers)) == 1


def test_option_wording_is_matched_when_offered(profile, facts):
    answer = answer_question(
        q("Are you willing to relocate?", type=QuestionType.BOOLEAN.value, options=["YES", "NO"]),
        profile,
        facts,
    )
    assert answer.value == "NO"
