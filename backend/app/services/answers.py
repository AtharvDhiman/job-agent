"""Screening-question answering.

An answer is only produced when it can be read straight off an explicit profile
field or a verified career fact. Anything else -- free text, ambiguity, an
unrecognised question, a protected characteristic -- returns needs_human=True
with a reason, which the workflow turns into a review task.

There is no "best guess" branch in this module. That is the point.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from app.core.enums import FactCategory, QuestionType
from app.models.profile import CandidateProfile, CareerFact
from app.utils.text import fold

EEO_DEFAULT = "Prefer not to say"
#: Wording platforms use for "I am not answering this". Every one of these is a
#: NON-disclosure; nothing that reveals a protected characteristic belongs here.
_EEO_DECLINE_RE = re.compile(
    r"prefer not|decline|do not wish|don'?t wish|not disclose|not to disclose|"
    r"choose not|rather not|opt out|no answer|not specified|undisclosed|"
    r"i do not want to (?:answer|self)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class Question:
    external_id: str = ""
    text: str = ""
    type: str = QuestionType.UNKNOWN.value
    required: bool = False
    options: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Answer:
    question: Question
    value: str | None = None
    source_fact_id: str | None = None
    source_field: str = ""
    confidence: int = 0
    needs_human: bool = True
    reason: str = ""

    def as_dict(self) -> dict:
        data = asdict(self)
        data["question"] = asdict(self.question)
        return data


# Question intent patterns. Order matters: the first match wins.
_INTENTS: list[tuple[str, re.Pattern]] = [
    ("full_name", re.compile(r"\b(full |legal )?name\b", re.I)),
    ("email", re.compile(r"\be-?mail\b", re.I)),
    ("phone", re.compile(r"\b(phone|mobile|telephone|contact number)\b", re.I)),
    ("location", re.compile(r"\b(city|current location|where are you (based|located))\b", re.I)),
    ("linkedin", re.compile(r"\blinked-?in\b", re.I)),
    ("github", re.compile(r"\b(github|git hub)\b", re.I)),
    ("portfolio", re.compile(r"\b(portfolio|personal (web)?site|website|other url)\b", re.I)),
    (
        "work_authorization",
        re.compile(
            r"\b(legally (authoriz|entitl)|authoriz(ed|ation) to work|right to work|"
            r"work permit|eligible to work)\b",
            re.I,
        ),
    ),
    (
        "sponsorship",
        re.compile(
            r"\b(require|need|now or in the future).{0,40}\bsponsorship\b|"
            r"\bsponsorship\b.{0,40}\b(require|need)\b",
            re.I,
        ),
    ),
    (
        "notice_period",
        re.compile(r"\b(notice period|when can you start|start date|availability)\b", re.I),
    ),
    (
        "salary_expectation",
        re.compile(
            r"\b(salary expectation|expected (salary|compensation)|desired (salary|pay)|"
            r"compensation expectation)\b",
            re.I,
        ),
    ),
    ("salary_history", re.compile(r"\b(current|previous|last) (salary|compensation|ctc)\b", re.I)),
    ("relocation", re.compile(r"\b(willing to relocate|relocation)\b", re.I)),
    ("remote", re.compile(r"\b(remote|work from home|hybrid|on-?site)\b", re.I)),
    (
        "years_experience",
        re.compile(r"\b(years? of (relevant )?experience|how many years)\b", re.I),
    ),
    ("references", re.compile(r"\breferences?\b", re.I)),
    (
        "eeo",
        re.compile(
            r"\b(gender|race|ethnic|veteran|disability|hispanic|latino|sexual orientation|"
            r"protected veteran|self-?identif)\b",
            re.I,
        ),
    ),
    ("how_did_you_hear", re.compile(r"\bhow did you (hear|find)\b", re.I)),
    (
        "cover_letter",
        re.compile(
            r"\b(cover letter|why do you want|tell us (about|why)|"
            r"what interests you)\b",
            re.I,
        ),
    ),
]


def classify(question: Question) -> str:
    text = question.text or ""
    for intent, pattern in _INTENTS:
        if pattern.search(text):
            return intent
    return "unknown"


def _fact_of(facts: list[CareerFact], category: str) -> CareerFact | None:
    for fact in facts:
        if fact.verified and fact.category == category:
            return fact
    return None


def _yes_no(question: Question, value: bool) -> str:
    """Match the platform's own option wording where it offers a choice."""
    positives, negatives = ("yes", "true", "y"), ("no", "false", "n")
    wanted = positives if value else negatives
    for option in question.options or []:
        if fold(option) in wanted:
            return option
    return "Yes" if value else "No"


def answer_question(
    question: Question, profile: CandidateProfile, facts: list[CareerFact]
) -> Answer:
    intent = classify(question)
    verified = [f for f in facts if f.verified]

    def ok(value, field_name: str, confidence: int = 100) -> Answer:
        return Answer(
            question=question,
            value=str(value),
            source_field=field_name,
            confidence=confidence,
            needs_human=False,
            reason="",
        )

    def escalate(reason: str) -> Answer:
        return Answer(question=question, needs_human=True, reason=reason)

    # Long free text is never auto-answered, whatever it asks.
    if question.type == QuestionType.LONG_TEXT.value and intent != "cover_letter":
        return escalate("Free-text question: drafted for you, not auto-filled.")

    if intent == "full_name" and profile.full_name:
        return ok(profile.full_name, "profile.full_name")
    if intent == "email" and profile.contact_email:
        return ok(profile.contact_email, "profile.contact_email")
    if intent == "phone" and profile.phone:
        return ok(profile.phone, "profile.phone")
    if intent == "location":
        parts = [profile.location_city, profile.location_region, profile.location_country]
        location = ", ".join(p for p in parts if p)
        return ok(location, "profile.location") if location else escalate("No location on file.")
    if intent == "linkedin":
        return (
            ok(profile.linkedin_url, "profile.linkedin_url")
            if profile.linkedin_url
            else escalate("No LinkedIn URL on file; we will not invent one.")
        )
    if intent in ("github", "portfolio"):
        urls = list(profile.portfolio_urls or [])
        if intent == "github":
            urls = [u for u in urls if "github.com" in u.lower()] or urls
        return (
            ok(urls[0], "profile.portfolio_urls")
            if urls
            else escalate("No portfolio link on file; we will not invent one.")
        )

    if intent == "work_authorization":
        auth = profile.work_authorization or {}
        if isinstance(auth, dict) and "authorized" in auth:
            return ok(_yes_no(question, bool(auth["authorized"])), "profile.work_authorization")
        fact = _fact_of(verified, FactCategory.WORK_AUTHORIZATION.value)
        if fact:
            answer = Answer(
                question=question,
                value=fact.value,
                source_fact_id=str(fact.id),
                source_field="career_fact.work_authorization",
                confidence=95,
                needs_human=False,
            )
            return answer
        return escalate(
            "Work authorization is not recorded. This can never be guessed -- answer it yourself."
        )

    if intent == "sponsorship":
        if profile.requires_sponsorship is None:
            return escalate("Sponsorship requirement is not recorded on your profile.")
        return ok(
            _yes_no(question, bool(profile.requires_sponsorship)), "profile.requires_sponsorship"
        )

    if intent == "notice_period":
        if profile.earliest_start_date:
            return ok(profile.earliest_start_date.isoformat(), "profile.earliest_start_date")
        if profile.notice_period_days is not None:
            return ok(f"{profile.notice_period_days} days", "profile.notice_period_days")
        return escalate("No notice period or start date on file.")

    if intent == "salary_expectation":
        if profile.min_salary_amount:
            return ok(
                f"{profile.min_salary_amount} {profile.min_salary_currency}"
                f" per {profile.salary_period}",
                "profile.min_salary_amount",
                confidence=85,
            )
        return escalate("No salary expectation on file.")

    if intent == "salary_history":
        return escalate(
            "Salary history is never auto-filled. It is sensitive, often unlawful to ask, "
            "and must come from you."
        )

    if intent == "relocation":
        # A missing preference is NOT a "no". Answering "I will not relocate"
        # for someone who never said so is as much a fabrication as inventing
        # an employer, and it silently costs them the role.
        if profile.willing_to_relocate is None:
            return escalate("Relocation preference is not recorded; it will not be assumed.")
        return ok(
            _yes_no(question, bool(profile.willing_to_relocate)), "profile.willing_to_relocate"
        )

    if intent == "remote":
        preferences = profile.work_arrangement_preference or []
        if not preferences:
            return escalate("No work-arrangement preference on file.")
        for option in question.options or []:
            if fold(option) in {fold(p) for p in preferences}:
                return ok(option, "profile.work_arrangement_preference")
        return ok(", ".join(preferences), "profile.work_arrangement_preference", confidence=80)

    if intent == "years_experience":
        if profile.years_experience is None:
            return escalate("Years of experience is not recorded; it will not be estimated.")
        return ok(str(profile.years_experience), "profile.years_experience", confidence=90)

    if intent == "references":
        refs = [f for f in verified if f.category == FactCategory.REFERENCE.value]
        if not refs:
            return escalate("No references on file. References are never invented.")
        return escalate("References exist but sharing them is your decision; confirm each time.")

    if intent == "eeo":
        return Answer(
            question=question,
            value=next(
                (o for o in (question.options or []) if _EEO_DECLINE_RE.search(o)), EEO_DEFAULT
            ),
            source_field="policy.eeo_default",
            confidence=100,
            needs_human=not question.required,
            reason=(
                "Protected characteristics are never inferred. Defaulted to "
                f"'{EEO_DEFAULT}'; change it yourself if you wish to disclose."
            ),
        )

    if intent == "how_did_you_hear":
        for option in question.options or []:
            if fold(option) in ("company website", "careers page", "job board", "other"):
                return ok(option, "policy.source_disclosure", confidence=70)
        return escalate("No matching option for how you found the role.")

    if intent == "cover_letter":
        return escalate("Cover-letter style question: a draft is attached for your review.")

    if question.type == QuestionType.BOOLEAN.value:
        return escalate("Yes/no question we could not map to a verified field.")

    return escalate(f"Unrecognised question; no verified fact maps to it (intent={intent}).")


def answer_all(
    questions: list[Question], profile: CandidateProfile, facts: list[CareerFact]
) -> list[Answer]:
    return [answer_question(q, profile, facts) for q in questions]


def blocking(answers: list[Answer]) -> list[Answer]:
    """Required questions we could not answer honestly."""
    return [a for a in answers if a.needs_human and a.question.required]
