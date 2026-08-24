"""Deterministic 0-100 match scoring with a complete, auditable explanation.

Design rules:
  * Hard filters run first and are absolute -- a failing job is never scored up.
  * Every point is attributable to a named component, so the UI can show the
    arithmetic rather than an opaque number.
  * No network and no LLM required. The optional LLM pass in llm.py only
    rewrites the prose explanation; it can never change the score.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from app.core.enums import MatchDecision, Seniority, WorkArrangement
from app.models.job import Job
from app.models.profile import CandidateProfile
from app.services.locations import expand_country_preferences
from app.services.taxonomy import canonicalize_skill
from app.utils.text import fold, normalize_company, tokenize

# Component weights. They sum to 100; changing one changes the whole scale, so
# the test suite asserts the sum.
WEIGHTS: dict[str, int] = {
    "skills": 35,
    "semantic": 20,
    "title": 15,
    "seniority": 10,
    "location": 10,
    "salary": 5,
    "freshness": 3,
    "direct_employer": 2,
}

SENIORITY_INDEX = {
    Seniority.INTERN: 0,
    Seniority.ENTRY: 1,
    Seniority.JUNIOR: 2,
    Seniority.MID: 3,
    Seniority.SENIOR: 4,
    Seniority.LEAD: 5,
    Seniority.STAFF: 5,
    Seniority.PRINCIPAL: 6,
    Seniority.MANAGER: 5,
    Seniority.DIRECTOR: 7,
    Seniority.EXECUTIVE: 8,
}

_STOPWORDS = frozenset(
    """a an and are as at be by for from has have in is it its of on or that the to
    with we you your our will their they this these those be being been can could
    should would about into over under more most other than then them us""".split()
)


@dataclass(slots=True)
class ScoreBreakdown:
    score: int = 0
    decision: str = MatchDecision.BELOW_THRESHOLD.value
    components: dict[str, float] = field(default_factory=dict)
    matching_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    hard_filter_failures: list[str] = field(default_factory=list)
    semantic_similarity: float = 0.0
    explanation: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


class SemanticIndex:
    """TF-IDF cosine over a job corpus.

    IDF is learned from the batch being scored, so common boilerplate ("we are
    an equal opportunity employer") is discounted automatically. With a single
    document the IDF collapses to 1 and this degrades to plain TF cosine, which
    is still a reasonable, stable signal.
    """

    def __init__(self, corpus: list[str] | None = None):
        self.doc_count = 0
        self.df: Counter[str] = Counter()
        for document in corpus or []:
            self.add(document)

    def add(self, document: str) -> None:
        self.doc_count += 1
        self.df.update(set(self._terms(document)))

    @staticmethod
    def _terms(text: str) -> list[str]:
        return [t for t in tokenize(text) if len(t) > 2 and t not in _STOPWORDS]

    def _idf(self, term: str) -> float:
        if self.doc_count == 0:
            return 1.0
        return math.log((1 + self.doc_count) / (1 + self.df.get(term, 0))) + 1.0

    def vector(self, text: str) -> dict[str, float]:
        counts = Counter(self._terms(text))
        if not counts:
            return {}
        vec = {t: (1 + math.log(c)) * self._idf(t) for t, c in counts.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    def similarity(self, a: str, b: str) -> float:
        va, vb = self.vector(a), self.vector(b)
        if not va or not vb:
            return 0.0
        smaller, larger = (va, vb) if len(va) < len(vb) else (vb, va)
        return max(0.0, min(1.0, sum(w * larger.get(t, 0.0) for t, w in smaller.items())))


def profile_text(profile: CandidateProfile, resume_text: str = "") -> str:
    """The candidate side of the comparison: preferences plus verified resume text."""
    parts = [
        profile.headline or "",
        " ".join(profile.target_titles or []),
        " ".join(profile.skills or []),
        " ".join(profile.industries_priority or []),
        resume_text or "",
    ]
    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------- hard filters
def evaluate_hard_filters(
    job: Job, profile: CandidateProfile, *, max_age_hours: int, now: datetime | None = None
) -> list[str]:
    """Absolute disqualifiers. Any hit means the job is rejected, not ranked."""
    now = now or datetime.now(UTC)
    failures: list[str] = []

    blocked = {normalize_company(c) for c in (profile.companies_to_avoid or []) if c}
    if blocked and job.company_normalized in blocked:
        failures.append(f"Company '{job.company}' is on your avoid list")

    haystack = fold(f"{job.title}\n{job.description_text[:8000]}")
    for keyword in profile.excluded_keywords or []:
        if keyword and fold(keyword) in haystack:
            failures.append(f"Contains excluded keyword '{keyword}'")

    allowed = expand_country_preferences(profile.preferred_countries)
    if allowed and job.location_country and job.location_country not in allowed:
        remote_ok = (
            job.work_arrangement == WorkArrangement.REMOTE.value
            and WorkArrangement.REMOTE.value in (profile.work_arrangement_preference or [])
        )
        if not remote_ok:
            failures.append(f"Location {job.location_country} is outside your preferred countries")

    preferences = set(profile.work_arrangement_preference or [])
    if preferences and job.work_arrangement != WorkArrangement.UNKNOWN.value:
        if job.work_arrangement not in preferences:
            failures.append(f"Work arrangement '{job.work_arrangement}' is not one you accept")

    if profile.requires_sponsorship and job.visa_sponsorship_mentioned is False:
        failures.append("Posting states it cannot sponsor and you require sponsorship")

    if profile.min_salary_amount and job.salary_max is not None:
        comparable = (
            not job.salary_currency
            or not profile.min_salary_currency
            or job.salary_currency == profile.min_salary_currency
        ) and (not job.salary_period or job.salary_period == profile.salary_period)
        if comparable and job.salary_max < profile.min_salary_amount:
            failures.append(
                f"Advertised maximum {job.salary_max} {job.salary_currency} is below your "
                f"minimum of {profile.min_salary_amount} {profile.min_salary_currency}"
            )

    types = set(profile.employment_types or [])
    if types and job.employment_type != "unknown" and job.employment_type not in types:
        failures.append(f"Employment type '{job.employment_type}' is not one you accept")

    reference = job.posted_at or job.first_seen_at
    if reference is not None:
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=UTC)
        if (now - reference).total_seconds() > max_age_hours * 3600:
            failures.append(f"Posted more than {max_age_hours}h ago")

    if job.deadline_at is not None:
        deadline = job.deadline_at
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        if deadline < now:
            failures.append("Application deadline has passed")

    if job.closed_at is not None:
        failures.append("Posting is closed")

    return failures


# ------------------------------------------------------------------ components
def _skill_component(job: Job, profile: CandidateProfile) -> tuple[float, list[str], list[str]]:
    have = {canonicalize_skill(s) for s in (profile.skills or []) if s}
    want = [canonicalize_skill(s) for s in (job.extracted_skills or []) if s]
    want_unique = list(dict.fromkeys(want))
    if not want_unique:
        # Nothing extractable: award a neutral half score rather than punishing a
        # sparse posting. The missing signal is reported as a risk instead.
        return 0.5, sorted(have)[:10], []
    matching = [s for s in want_unique if s in have]
    missing = [s for s in want_unique if s not in have]
    return len(matching) / len(want_unique), matching, missing


def _title_component(job: Job, profile: CandidateProfile) -> float:
    targets = [t for t in (profile.target_titles or []) if t]
    if not targets:
        return 0.5
    job_tokens = set(tokenize(job.title_normalized or job.title))
    best = 0.0
    for target in targets:
        target_tokens = set(tokenize(target))
        if not target_tokens:
            continue
        best = max(best, len(job_tokens & target_tokens) / len(target_tokens))
    return min(1.0, best)


def _seniority_component(job: Job, profile: CandidateProfile) -> tuple[float, str | None]:
    try:
        job_level = Seniority(job.seniority)
        my_level = Seniority(profile.seniority_level)
    except ValueError:
        return 0.5, None
    if job_level == Seniority.UNKNOWN or my_level == Seniority.UNKNOWN:
        return 0.5, "Seniority could not be determined from the posting"
    gap = SENIORITY_INDEX.get(job_level, 3) - SENIORITY_INDEX.get(my_level, 3)
    if gap == 0:
        return 1.0, None
    if gap == 1:
        return 0.75, "One level above your stated seniority (a stretch role)"
    if gap == -1:
        return 0.6, "One level below your stated seniority"
    if gap > 1:
        return 0.15, f"{gap} levels above your stated seniority"
    return 0.25, f"{abs(gap)} levels below your stated seniority"


def _location_component(job: Job, profile: CandidateProfile) -> tuple[float, str | None]:
    preferences = set(profile.work_arrangement_preference or [])
    allowed = expand_country_preferences(profile.preferred_countries)
    if job.work_arrangement == WorkArrangement.REMOTE.value:
        if not preferences or WorkArrangement.REMOTE.value in preferences:
            return 1.0, None
        return 0.4, "Remote role but you prefer on-site or hybrid"
    if not job.location_country:
        return 0.5, "Location could not be resolved from the posting"
    if not allowed or job.location_country in allowed:
        same_city = bool(job.location_city) and fold(job.location_city) == fold(
            profile.location_city
        )
        return (1.0 if same_city else 0.8), None
    if profile.willing_to_relocate:
        return 0.5, f"Outside your preferred countries ({job.location_country}); relocation needed"
    return 0.2, f"Outside your preferred countries ({job.location_country})"


def _salary_component(job: Job, profile: CandidateProfile) -> tuple[float, str | None]:
    if not profile.min_salary_amount:
        return 0.6, None
    if job.salary_min is None and job.salary_max is None:
        return 0.5, "No salary published; confirm before applying"
    if (
        job.salary_currency
        and profile.min_salary_currency
        and job.salary_currency != profile.min_salary_currency
    ):
        return 0.5, (
            f"Salary is quoted in {job.salary_currency} but your minimum is in "
            f"{profile.min_salary_currency}; not directly comparable"
        )
    top = job.salary_max or job.salary_min or 0
    if top >= profile.min_salary_amount * 1.25:
        return 1.0, None
    if top >= profile.min_salary_amount:
        return 0.8, None
    return 0.3, f"Advertised pay tops out below your minimum ({top} {job.salary_currency})"


def _freshness_component(job: Job, now: datetime) -> float:
    reference = job.posted_at or job.first_seen_at
    if reference is None:
        return 0.5
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    hours = max(0.0, (now - reference).total_seconds() / 3600)
    if hours <= 24:
        return 1.0
    if hours <= 48:
        return 0.8
    if hours <= 72:
        return 0.6
    return 0.3


# --------------------------------------------------------------------- scoring
def score_job(
    job: Job,
    profile: CandidateProfile,
    *,
    resume_text: str = "",
    index: SemanticIndex | None = None,
    max_age_hours: int = 48,
    shortlist_min_score: int = 60,
    now: datetime | None = None,
) -> ScoreBreakdown:
    now = now or datetime.now(UTC)
    breakdown = ScoreBreakdown()

    failures = evaluate_hard_filters(job, profile, max_age_hours=max_age_hours, now=now)
    if failures:
        breakdown.hard_filter_failures = failures
        breakdown.score = 0
        breakdown.decision = _rejection_decision(failures)
        breakdown.explanation = "Rejected before scoring: " + "; ".join(failures)
        return breakdown

    index = index or SemanticIndex([job.description_text])
    skills_ratio, matching, missing = _skill_component(job, profile)
    title_ratio = _title_component(job, profile)
    seniority_ratio, seniority_risk = _seniority_component(job, profile)
    location_ratio, location_risk = _location_component(job, profile)
    salary_ratio, salary_risk = _salary_component(job, profile)
    freshness_ratio = _freshness_component(job, now)
    similarity = index.similarity(
        profile_text(profile, resume_text), f"{job.title}\n{job.description_text}"
    )
    direct_ratio = 1.0 if job.is_direct_employer else 0.0

    ratios = {
        "skills": skills_ratio,
        "semantic": similarity,
        "title": title_ratio,
        "seniority": seniority_ratio,
        "location": location_ratio,
        "salary": salary_ratio,
        "freshness": freshness_ratio,
        "direct_employer": direct_ratio,
    }
    components = {name: round(ratios[name] * weight, 2) for name, weight in WEIGHTS.items()}
    total = round(sum(components.values()))

    risks: list[str] = [r for r in (seniority_risk, location_risk, salary_risk) if r]
    if not job.extracted_skills:
        risks.append("No recognisable skills could be extracted from the posting")
    if job.visa_sponsorship_mentioned is None and profile.requires_sponsorship:
        risks.append("Posting does not say whether it sponsors visas; you need sponsorship")
    if not job.is_direct_employer:
        risks.append("Listed through an aggregator rather than the employer's own board")
    if job.deadline_at is not None:
        deadline = job.deadline_at
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        remaining = (deadline - now).total_seconds() / 86400
        if remaining <= 3:
            risks.append(f"Deadline in {remaining:.1f} days")
    if len(missing) > len(matching):
        risks.append(f"{len(missing)} of the posting's skills are not on your verified profile")

    breakdown.score = max(0, min(100, total))
    breakdown.components = components
    breakdown.matching_skills = matching
    breakdown.missing_skills = missing
    breakdown.risks = risks
    breakdown.semantic_similarity = round(similarity, 4)
    breakdown.decision = (
        MatchDecision.SHORTLISTED.value
        if breakdown.score >= shortlist_min_score
        else MatchDecision.BELOW_THRESHOLD.value
    )
    breakdown.explanation = build_explanation(job, profile, breakdown)
    return breakdown


def _rejection_decision(failures: list[str]) -> str:
    joined = " ".join(failures).lower()
    if "avoid list" in joined:
        return MatchDecision.EXCLUDED_COMPANY.value
    if "excluded keyword" in joined:
        return MatchDecision.EXCLUDED_KEYWORD.value
    if "posted more than" in joined or "deadline" in joined or "closed" in joined:
        return MatchDecision.STALE_POSTING.value
    return MatchDecision.REJECTED_HARD_FILTER.value


def build_explanation(job: Job, profile: CandidateProfile, b: ScoreBreakdown) -> str:
    """Plain-language justification. This is what the dashboard shows."""
    lines = [f"Score {b.score}/100 for {job.title} at {job.company}."]
    ordered = sorted(b.components.items(), key=lambda kv: -kv[1])
    lines.append(
        "Points: " + ", ".join(f"{name} {value:g}/{WEIGHTS[name]}" for name, value in ordered)
    )
    if b.matching_skills:
        lines.append(f"Matching skills ({len(b.matching_skills)}): {', '.join(b.matching_skills)}")
    else:
        lines.append("Matching skills: none of the posting's skills are on your profile")
    lines.append(
        f"Missing skills ({len(b.missing_skills)}): {', '.join(b.missing_skills)}"
        if b.missing_skills
        else "Missing skills: none, your profile covers every skill the posting names"
    )
    lines.append(
        f"Seniority: posting '{job.seniority}' vs your '{profile.seniority_level}'. "
        f"Location: {job.location_raw or 'unspecified'} ({job.work_arrangement}). "
        f"Salary: {_salary_phrase(job)}."
    )
    lines.append(
        "Work authorization: "
        + (
            "posting says sponsorship is available"
            if job.visa_sponsorship_mentioned is True
            else "posting says it cannot sponsor"
            if job.visa_sponsorship_mentioned is False
            else "not stated in the posting"
        )
        + "."
    )
    if b.risks:
        lines.append("Risks: " + "; ".join(b.risks))
    lines.append(
        "Applies directly with the employer."
        if job.is_direct_employer
        else "Routed through a third-party listing."
    )
    return "\n".join(lines)


def _salary_phrase(job: Job) -> str:
    if job.salary_min is None and job.salary_max is None:
        return "not published"
    low = f"{job.salary_min:,}" if job.salary_min is not None else "?"
    high = f"{job.salary_max:,}" if job.salary_max is not None else "?"
    return f"{low}-{high} {job.salary_currency} per {job.salary_period or 'year'}".strip()
