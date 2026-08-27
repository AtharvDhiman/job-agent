"""Second pass over a generated document: what did it leave on the table?

fact_guard already answers "does this document claim anything untrue". That is a
safety question and it has to block. This module answers a different, weaker
question: "is this document as strong as the candidate's own verified facts
allow it to be". That is a quality question, so it never blocks -- it advises.

The separation matters because the two failure modes are opposite. fact_guard
exists to stop the document saying MORE than the facts support. The critic
exists to notice it said LESS.

Nothing here may add content. Every finding either points at a fact the
candidate has already verified, or reports a measurement of the text. A critic
that could write would reintroduce exactly the fabrication risk the rest of the
pipeline is built to prevent, so `suggestion` is always drawn from stored
strings and the caller is expected to surface it to a human, never to splice it
in automatically.

Deterministic on purpose: this runs with no ANTHROPIC_API_KEY set, like the
rest of the drafting path.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app.models.job import Job
from app.models.profile import CandidateProfile, CareerFact
from app.services.taxonomy import canonicalize_skill
from app.utils.text import fold, tokenize

SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"

#: A resume much shorter than this is usually missing a section; much longer and
#: the relevance-weighted trim in document_generator should have cut more.
_RESUME_MIN_WORDS = 120
_RESUME_MAX_WORDS = 900


@dataclass(slots=True)
class Finding:
    kind: str
    detail: str
    severity: str = SEVERITY_MEDIUM
    #: Verbatim stored string the human could choose to use. Never generated
    #: prose -- see the module docstring.
    suggestion: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class CritiqueReport:
    findings: list[Finding] = field(default_factory=list)
    #: How many of the posting's own skills the document actually mentions.
    keyword_coverage: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)
    missing_keywords: list[str] = field(default_factory=list)
    word_count: int = 0

    @property
    def score(self) -> int:
        """0-100 readability of the critique, for sorting review queues.

        Deliberately blunt: coverage is what a recruiter's ATS keys on, and
        each high-severity finding is worth a visible dent.
        """
        base = self.keyword_coverage * 100
        penalty = sum(
            {SEVERITY_HIGH: 12, SEVERITY_MEDIUM: 5, SEVERITY_LOW: 2}.get(f.severity, 0)
            for f in self.findings
        )
        return max(0, min(100, round(base - penalty)))

    def as_dict(self) -> dict:
        return {
            "score": self.score,
            "keyword_coverage": round(self.keyword_coverage, 3),
            "matched_keywords": self.matched_keywords,
            "missing_keywords": self.missing_keywords,
            "word_count": self.word_count,
            "findings": [f.as_dict() for f in self.findings],
        }


def _document_skills(body: str) -> tuple[set[str], str]:
    """Canonical skills the document actually mentions.

    Canonicalising both sides is what makes 'ML' in the posting match 'machine
    learning' in the resume, and what stops 'MachineLearning' matching either.
    """
    folded = fold(body)
    tokens = set(tokenize(body))
    found: set[str] = set()
    for token in tokens:
        found.add(canonicalize_skill(token))
    # Multi-word skills ("machine learning") never survive tokenisation, so
    # check those as substrings of the folded text instead.
    return found, folded


def _verified_skill_facts(facts: list[CareerFact]) -> dict[str, CareerFact]:
    """Canonical skill -> the verified fact that evidences it."""
    out: dict[str, CareerFact] = {}
    for fact in facts:
        if not fact.verified:
            continue
        for tag in fact.tags or []:
            out.setdefault(canonicalize_skill(tag), fact)
    return out


def critique(
    body: str,
    *,
    job: Job,
    profile: CandidateProfile,
    facts: list[CareerFact],
    kind: str = "resume",
) -> CritiqueReport:
    """Compare a generated document against the posting it targets."""
    report = CritiqueReport()
    words = body.split()
    report.word_count = len(words)

    token_skills, folded_body = _document_skills(body)

    wanted = [canonicalize_skill(s) for s in (job.extracted_skills or []) if s]
    wanted = list(dict.fromkeys(w for w in wanted if w))

    matched: list[str] = []
    missing: list[str] = []
    for skill in wanted:
        present = skill in token_skills or skill in folded_body
        (matched if present else missing).append(skill)

    report.matched_keywords = matched
    report.missing_keywords = missing
    report.keyword_coverage = (len(matched) / len(wanted)) if wanted else 1.0

    # --- the findings that actually matter -------------------------------
    # Two different failures wear the same face -- a posting skill the document
    # does not mention -- and conflating them gives the user the wrong job.
    #
    #   * Backed by a VERIFIED fact: the evidence exists and the document still
    #     dropped it. Something is wrong with the drafting.
    #   * Present only in profile.skills: that list is a self-declaration used
    #     for RANKING. It is not document source material -- it is not in
    #     fact_guard's profile corpus, and the resume generator emits skills
    #     only from verified skill facts. So the document is behaving correctly
    #     and the fix is to verify a fact, not to change the generator.
    #
    # Calling the second case "verified" would blame the drafter for enforcing
    # the guarantee this whole pipeline is built on.
    profile_skills = {canonicalize_skill(s) for s in (profile.skills or []) if s}
    fact_skills = _verified_skill_facts(facts)
    for skill in missing:
        if skill in fact_skills:
            fact = fact_skills[skill]
            source = " at ".join(p for p in (fact.title or fact.key, fact.organization) if p)
            report.findings.append(
                Finding(
                    kind="verified_skill_omitted",
                    detail=(
                        f"The posting asks for '{skill}' and you have it verified "
                        f"({source}), but the {kind} never mentions it."
                    ),
                    severity=SEVERITY_HIGH,
                    suggestion=skill,
                )
            )
        elif skill in profile_skills:
            report.findings.append(
                Finding(
                    kind="skill_claimed_but_not_evidenced",
                    detail=(
                        f"The posting asks for '{skill}' and it is listed in your "
                        f"profile, but no verified fact evidences it -- so the "
                        f"{kind} cannot claim it. Verify a fact that mentions "
                        f"'{skill}' and it will appear."
                    ),
                    severity=SEVERITY_MEDIUM,
                    suggestion=skill,
                )
            )

    # --- coverage --------------------------------------------------------
    if wanted and report.keyword_coverage < 0.4:
        report.findings.append(
            Finding(
                kind="low_keyword_coverage",
                detail=(
                    f"The {kind} mentions {len(matched)} of the posting's "
                    f"{len(wanted)} skills. Most ATS keyword screens would rank this low."
                ),
                severity=SEVERITY_HIGH,
            )
        )

    # --- length ----------------------------------------------------------
    if kind == "resume":
        if report.word_count < _RESUME_MIN_WORDS:
            report.findings.append(
                Finding(
                    kind="too_short",
                    detail=(
                        f"Only {report.word_count} words. That usually means a section "
                        "is empty because too few facts are verified."
                    ),
                    severity=SEVERITY_HIGH,
                )
            )
        elif report.word_count > _RESUME_MAX_WORDS:
            report.findings.append(
                Finding(
                    kind="too_long",
                    detail=(
                        f"{report.word_count} words is long enough that the relevance "
                        "trim should have cut more."
                    ),
                    severity=SEVERITY_LOW,
                )
            )

    # --- contactability --------------------------------------------------
    if not (profile.contact_email and fold(profile.contact_email) in folded_body):
        report.findings.append(
            Finding(
                kind="missing_contact",
                detail=f"The {kind} does not carry your contact email.",
                severity=SEVERITY_HIGH if kind == "resume" else SEVERITY_LOW,
            )
        )

    # --- title alignment -------------------------------------------------
    title_tokens = set(tokenize(job.title or ""))
    if title_tokens and not (title_tokens & set(tokenize(body))):
        report.findings.append(
            Finding(
                kind="title_not_echoed",
                detail=(
                    f"Nothing in the {kind} echoes the posting's title "
                    f"('{job.title}'). Recruiters skim for it."
                ),
                severity=SEVERITY_MEDIUM,
            )
        )

    return report
