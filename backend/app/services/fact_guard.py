"""Post-generation truthfulness check.

Anything a generator writes is compared back against the VERIFIED career facts.
Claims that cannot be traced to a fact are flagged; a blocking flag makes the
document ineligible for auto-submission and routes it to human review.

This runs regardless of whether the text came from the LLM or the deterministic
templates, so a model that ignores its instructions still cannot ship a lie.

Detection is deliberately biased towards false positives: a flag costs a review
task, a miss costs a fabricated application. Every rule below therefore fails
closed -- if a claim cannot be traced to a verified fact (or, for answers, to an
explicit profile field), it is flagged.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from app.core.enums import FactCategory
from app.models.profile import CandidateProfile, CareerFact
from app.utils.text import fold, normalize_company

# A sentinel the model emits instead of guessing, not a credential.
INSUFFICIENT_FACTS_TOKEN = "INSUFFICIENT_FACTS"  # noqa: S105

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
#: Scheme-less links ("github.com/someone-else") are the common way a fabricated
#: portfolio link reaches a resume, so they are matched too.
_TLDS = (
    "com|net|org|io|dev|me|co|ai|app|xyz|edu|gov|info|biz|us|uk|ca|de|fr|in|au|nl|se|no|"
    "es|it|ch|jp|tech|cloud|page|site|blog|sh|gg|to|ly"
)
_BARE_URL_RE = re.compile(
    rf"\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:{_TLDS})\b(?:/[^\s<>\"')\]]*)?",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
#: Every digit group in the text. A number nobody verified is a fabricated
#: metric even when it carries a unit this file never anticipated ("40ms").
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
#: Trailing unit, captured only to make the flag message readable.
_UNIT_RE = re.compile(r"\s?(%|[A-Za-z]{1,12})\b")
_WORDED_METRIC_RE = re.compile(
    r"\b(doubled|tripled|quadrupled|quintupled|halved|two-?fold|three-?fold|four-?fold|"
    r"ten-?fold|orders? of magnitude|dozens of|hundreds of|thousands of|millions of|"
    r"billions of)\b",
    re.IGNORECASE,
)
_CREDENTIAL_RE = re.compile(
    r"\b(ph\.?d|doctorate|mba|m\.?sc?|b\.?sc?|b\.?tech|m\.?tech|bachelor'?s?|master'?s?|"
    r"aws certified|azure certified|gcp certified|pmp|cissp|cpa|cfa|scrum master|"
    r"certified [a-z ]{3,40}|"
    r"ckad|cka|cks|ccna|ccnp|rhce|rhcsa|oscp|security\+|network\+|"
    r"(?:aws|azure|gcp|google cloud|kubernetes|cisco|oracle|salesforce|comptia|red hat)\s+"
    r"(?:[a-z]+\s+){0,3}"
    r"(?:certified|certification|architect|administrator|practitioner|associate|"
    r"professional|expert|specialty)|"
    r"(?:passed|completed|earned|obtained|achieved|holds?)\s+(?:the\s+)?"
    r"[A-Za-z][\w.+#-]*(?:\s+[A-Za-z][\w.+#-]*){0,5}\s+"
    r"(?:exam|certification|certificate|credential|licen[cs]e|designation)"
    r")\b",
    re.IGNORECASE,
)
# Capitalised run after an employment preposition. A trailing period ends the
# run, so "at Northwind Systems. I built..." captures "Northwind Systems" and
# does not swallow the following sentence.
_EMPLOYMENT_CLAIM_RE = re.compile(
    r"\b(?:at|for|with|joined)\s+([A-Z][\w&'-]+(?:\s+[A-Z][\w&'-]+){0,3})",
)
#: "Ex-Google", "former Meta", "formerly at Stripe" -- the headline shape of a
#: fabricated employer, which carries no preposition of its own.
_EX_EMPLOYER_RE = re.compile(
    r"\b(?i:ex|former|formerly)[-\s](?:(?i:at)\s+)?([A-Z][\w&'.-]*(?:\s+[A-Z][\w&'.-]*){0,3})"
)
#: An employer named in lower case after a preposition. Restricted to hard legal
#: suffixes so that ordinary engineering prose ("with legacy systems") is not
#: mistaken for an employment claim.
_LOWERCASE_ORG_RE = re.compile(
    r"\b(?:at|for|with|joined)\s+"
    r"([\w&'.-]+(?:\s+[\w&'.-]+){0,3}\s+"
    r"(?:corp|corporation|inc|incorporated|llc|ltd|limited|gmbh|plc|pty)\.?)(?!\w)",
    re.IGNORECASE,
)
#: An employer named WITHOUT a preposition -- "Acme Corp - Senior Engineer" --
#: is the shape a fabricated resume line actually takes.
_CORPORATE_SUFFIXES = (
    r"Incorporated|Inc\.|Inc|L\.L\.C\.|LLC|Limited|Ltd\.|Ltd|Corporation|Corp\.|Corp|"
    r"Company|GmbH|PLC|S\.A\.|B\.V\.|Pty|SARL|SRL|SpA|Pte|Holdings|Group|Technologies|"
    r"Technology|Systems|Labs|Laboratories|Solutions|Software|Industries|Ventures|"
    r"Partners|Capital|Networks|Consulting|Analytics|University|College|Institute|"
    r"Academy|Hospital|Foundation"
)
_SUFFIX_ORG_RE = re.compile(
    rf"\b([A-Z][\w&'.-]*(?:\s+[A-Z][\w&'.-]*){{0,3}}\s+(?:{_CORPORATE_SUFFIXES}))(?!\w)"
)
#: A job title claimed for the candidate. COMPLIANCE.md says titles are checked;
#: they were not, so "Principal Engineer" on top of a verified "Senior Backend
#: Engineer" fact used to ship. At least one capitalised qualifier is required so
#: ordinary prose ("I lead the team") is not read as a title claim.
#: Note the [ \t]+ separator rather than \s+: a title claim may not span a line
#: break, or a markdown heading fuses with the line beneath it and "## Summary"
#: followed by "Backend engineer" is read as the single title "Summary Backend
#: engineer".
_TITLE_CLAIM_RE = re.compile(
    r"\b((?:[A-Z][\w.&-]*[ \t]+){1,3}"
    r"(?i:engineer|developer|manager|director|architect|analyst|scientist|designer|"
    r"consultant|administrator|specialist|officer|president|lead|head|founder|intern|"
    r"associate|coordinator|supervisor|technician|programmer|researcher|strategist|"
    r"executive|recruiter|accountant|attorney|nurse|teacher|professor)s?)\b"
)
_TITLE_WORD_RE = re.compile(
    r"\b(engineer|engineering|developer|manager|director|analyst|designer|scientist|"
    r"consultant|architect|administrator|specialist|officer|president|founder|lead|head|"
    r"intern|associate|coordinator|supervisor|technician|programmer|researcher|"
    r"strategist|executive|recruiter|accountant|attorney|nurse|teacher|professor|"
    r"cto|ceo|cfo|coo|vp)\b",
    re.IGNORECASE,
)
#: "<Employer> - <Job title>" or "<Job title>, <Employer>" on its own line.
_ROLE_LINE_RE = re.compile(
    r"^\s*(?:[-*•]\s*|#{1,6}\s*)?"
    r"(?P<a>[A-Z][\w&'.-]*(?:\s+[A-Z][\w&'.-]*){0,3})"
    r"\s*(?:[-–—|]|,)\s+"
    r"(?P<b>[A-Za-z][\w&'.-]*(?:\s+[\w&'.-]+){0,5})\s*$"
)
#: "<Employer>, 2019-2021" on its own line.
_ORG_DATE_LINE_RE = re.compile(
    r"^\s*(?:[-*•]\s*|#{1,6}\s*)?"
    r"(?P<org>[A-Z][\w&'.-]*(?:\s+[A-Z][\w&'.-]*){0,3})"
    r"\s*[,|–—-]\s*"
    r"(?:[A-Z][a-z]{2,8}\.?\s+)?(?:19|20)\d{2}(?!\w)"
)
#: Immigration and right-to-work vocabulary. Deliberately wider than the phrases
#: a model is likely to use, because every one of these is unguessable.
_WORK_AUTH_RE = re.compile(
    r"\b(?:green[ -]?card|permanent resident(?:cy|ce|s)?|permanent residency|"
    r"work (?:visa|permit|authoriz\w+|eligibility)|visas?|sponsorship|sponsored|"
    r"citizen(?:ship)?s?|nationality|right to work|authoriz\w+ to work|eligible to work|"
    r"h-?1-?b|l-?1[ab]?|o-?1|e-?3|tn status|blue card|"
    r"indefinite leave to remain|settled status|naturali[sz]ed|"
    r"work(?:ing)? holiday|residence permit)\b",
    re.IGNORECASE,
)
#: Compensation history / current pay. Never generated, never inferred.
_SALARY_CLAIM_RE = re.compile(
    r"\b(?:my|current|previous|last|present|existing)\s+"
    r"(?:base\s+|total\s+|annual\s+|monthly\s+|gross\s+|net\s+)?"
    r"(?:salary|salaries|compensation|comp|pay|paycheck|package|ctc|remuneration|earnings)\b"
    r"|\bi\s+(?:currently\s+)?(?:earn|earned|make|made|was paid|am paid|take home)\b"
    r"|\bsalary (?:history|expectations?)\b"
    r"|\bpaid\s+(?:me\s+)?[\$€£₹¥]",
    re.IGNORECASE,
)
_REFERENCE_RE = re.compile(r"\b(references?|referees?|vouch(?:ed|es)?)\b", re.IGNORECASE)
#: Saying "references available on request" discloses nothing about anybody.
_REFERENCE_BOILERPLATE_RE = re.compile(
    r"references (?:are )?available(?: (?:up)?on request)?"
    r"|(?:happy|glad|able) to (?:provide|share|supply) references"
    r"|i can provide references",
    re.IGNORECASE,
)

SEVERITY_BLOCK = "block"
SEVERITY_WARN = "warn"


@dataclass(slots=True)
class Flag:
    kind: str
    span: str
    reason: str
    severity: str = SEVERITY_BLOCK

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class GuardReport:
    flags: list[Flag] = field(default_factory=list)
    checked_characters: int = 0

    @property
    def blocked(self) -> bool:
        return any(f.severity == SEVERITY_BLOCK for f in self.flags)

    def as_dict(self) -> dict:
        return {
            "blocked": self.blocked,
            "checked_characters": self.checked_characters,
            "flags": [f.as_dict() for f in self.flags],
        }


def _number_key(raw: str) -> str:
    """'2,000' -> '2000', '7.0' -> '7'. Comparable across formatting."""
    cleaned = raw.replace(",", "")
    try:
        value = float(cleaned)
    except ValueError:
        return cleaned
    return str(int(value)) if value.is_integer() else repr(value)


def _numbers_in(text: str) -> set[str]:
    return {_number_key(m.group(0)) for m in _NUMBER_RE.finditer(text or "")}


def _url_key(url: str) -> str:
    """Scheme-less, www-less, lowercase, no trailing punctuation."""
    key = (url or "").strip().rstrip("/.,);:'\"").lower()
    key = re.sub(r"^https?://", "", key)
    return re.sub(r"^www\.", "", key)


def _url_is_known(candidate: str, known: set[str]) -> bool:
    """A known link, or a path underneath one. Never a lookalike prefix.

    'github.com/testowner-evil' must NOT pass because 'github.com/testowner'
    is on file: only a '/' boundary counts as being the same link.
    """
    for entry in known:
        if candidate == entry:
            return True
        if candidate.startswith(entry) and candidate[len(entry) :][:1] in ("/", "?", "#"):
            return True
        if entry.startswith(candidate) and entry[len(candidate) :][:1] in ("/", "?", "#"):
            return True
    return False


def _clause_at(text: str, start: int, end: int, limit: int = 140) -> str:
    """The sentence-ish slice around a match, for a readable flag span."""
    left = max((text.rfind(c, 0, start) for c in ".;\n!?"), default=-1)
    right = min(
        (pos for pos in (text.find(c, end) for c in ".;\n!?") if pos != -1),
        default=len(text),
    )
    return text[left + 1 : right].strip()[:limit] or text[start:end]


class FactIndex:
    """Everything the candidate has actually verified, in comparable form.

    Two corpora are kept apart on purpose:

    * ``folded_corpus`` -- verified career facts. Generated DOCUMENTS are
      checked against this and nothing else.
    * ``folded_profile`` -- explicit profile fields the human typed (name,
      contact details, stated salary floor, stated years of experience).
      Screening ANSWERS may draw on these, because answering "what is your
      phone number" from the phone number you entered is not a fabrication.
    """

    def __init__(self, profile: CandidateProfile, facts: list[CareerFact]):
        self.profile = profile
        self.facts = [f for f in facts if f.verified]
        self.corpus = " \n ".join(
            " ".join(
                filter(
                    None,
                    [
                        f.key or "",
                        f.value or "",
                        f.organization or "",
                        f.title or "",
                        f.location or "",
                        " ".join(f.highlights or []),
                        " ".join(f.tags or []),
                        f.evidence_url or "",
                    ],
                )
            )
            for f in self.facts
        )
        self.folded_corpus = fold(self.corpus)
        self.profile_corpus = " \n ".join(
            str(value)
            for value in (
                profile.full_name,
                # The headline is the candidate's own self-description, typed by
                # them and echoed verbatim by the resume generator. Echoing what
                # the human wrote is not the generator inventing something, so it
                # counts as profile source material exactly like the name does.
                profile.headline,
                profile.contact_email,
                profile.phone,
                profile.location_city,
                profile.location_region,
                profile.location_country,
                profile.min_salary_amount,
                profile.min_salary_currency,
                profile.years_experience,
                profile.notice_period_days,
                profile.earliest_start_date,
            )
            if value not in (None, "")
        )
        self.folded_profile = fold(self.profile_corpus)
        self.organizations = {
            normalize_company(f.organization) for f in self.facts if f.organization
        }
        self.years: set[str] = set(_YEAR_RE.findall(self.corpus))
        for fact in self.facts:
            for value in (fact.start_date, fact.end_date):
                if value:
                    self.years.add(str(value.year))
        #: Numbers the human actually recorded, from facts AND profile fields.
        #: The resume's own contact line carries the phone number, so profile
        #: digits have to count as known or every document would self-flag.
        self.numbers = _numbers_in(self.corpus) | _numbers_in(self.profile_corpus)
        self.urls = {
            url.rstrip("/").lower()
            for url in (
                list(profile.portfolio_urls or [])
                + ([profile.linkedin_url] if profile.linkedin_url else [])
                + [f.evidence_url for f in self.facts if f.evidence_url]
                + _URL_RE.findall(self.corpus)
            )
            if url
        }
        self.url_keys = {_url_key(u) for u in self.urls}
        self.credentials = fold(
            " ".join(
                f.value
                for f in self.facts
                if f.category in (FactCategory.EDUCATION.value, FactCategory.CERTIFICATION.value)
            )
        )
        self.has_work_authorization_fact = any(
            f.category == FactCategory.WORK_AUTHORIZATION.value for f in self.facts
        )
        self.reference_facts = [f for f in self.facts if f.category == FactCategory.REFERENCE.value]

    def mentions(self, needle: str) -> bool:
        return bool(needle) and fold(needle) in self.folded_corpus

    def profile_mentions(self, needle: str) -> bool:
        return bool(needle) and fold(needle) in self.folded_profile

    def knows_number(self, raw: str) -> bool:
        return _number_key(raw) in self.numbers


_COMMON_WORDS = frozenset(
    """i my me we our the a an and or but so then this that these those it its
    monday tuesday wednesday thursday friday saturday sunday january february march
    april may june july august september october november december dear sincerely
    regards hello hi thank thanks best kind yours team role position company
    opportunity experience skills""".split()
)
#: Words that may lead a captured run but are never part of the employer name.
_LEADING_NOISE = frozenset(
    """dear the a an at for with joined my our i to in of and from this that hi
    hello sincerely regards best kind yours thank thanks am is was were""".split()
)


def _strip_leading_noise(candidate: str) -> str:
    tokens = candidate.split()
    while tokens and fold(tokens[0]) in _LEADING_NOISE:
        tokens.pop(0)
    return " ".join(tokens)


def _looks_like_org(candidate: str) -> bool:
    tokens = candidate.split()
    return bool(tokens) and len(tokens) <= 4 and all(t[:1].isupper() for t in tokens)


def _employer_candidates(text: str) -> list[str]:
    """Every span the text presents as somewhere the candidate worked."""
    found: list[str] = []

    for match in _EMPLOYMENT_CLAIM_RE.finditer(text):
        found.append(match.group(1).strip())

    for match in _EX_EMPLOYER_RE.finditer(text):
        found.append(_strip_leading_noise(match.group(1).strip()))

    for match in _LOWERCASE_ORG_RE.finditer(text):
        found.append(_strip_leading_noise(match.group(1).strip()))

    for match in _SUFFIX_ORG_RE.finditer(text):
        found.append(_strip_leading_noise(match.group(1).strip()))

    for line in (text or "").splitlines():
        if len(line) > 120:
            continue
        role = _ROLE_LINE_RE.match(line)
        if role is not None:
            left, right = role.group("a").strip(), role.group("b").strip()
            left_title = bool(_TITLE_WORD_RE.search(left))
            right_title = bool(_TITLE_WORD_RE.search(right))
            if right_title and not left_title:
                found.append(_strip_leading_noise(left))
            elif left_title and not right_title and _looks_like_org(right):
                found.append(_strip_leading_noise(right))
        dated = _ORG_DATE_LINE_RE.match(line)
        if dated is not None:
            found.append(_strip_leading_noise(dated.group("org").strip()))

    ordered: dict[str, str] = {}
    for candidate in found:
        key = normalize_company(candidate)
        if key and key not in ordered:
            ordered[key] = candidate
    return list(ordered.values())


def check(
    text: str,
    index: FactIndex,
    *,
    target_company: str = "",
    target_title: str = "",
    allow_target_company: bool = True,
    allow_profile_fields: bool = False,
    allow_sourced_claims: bool = False,
) -> GuardReport:
    """Flag every claim in `text` that cannot be traced to a verified fact.

    ``allow_profile_fields`` lets a screening answer quote an explicit profile
    field. ``allow_sourced_claims`` is set only when the whole value was copied
    verbatim out of one verified fact, which is the only situation in which a
    work-authorization sentence may appear at all.
    """
    report = GuardReport(checked_characters=len(text or ""))
    if not text:
        return report

    def known(span: str) -> bool:
        return index.mentions(span) or (allow_profile_fields and index.profile_mentions(span))

    def add(kind: str, span: str, reason: str, severity: str = SEVERITY_BLOCK) -> None:
        if any(f.kind == kind and f.span == span for f in report.flags):
            return
        report.flags.append(Flag(kind=kind, span=span, reason=reason, severity=severity))

    if INSUFFICIENT_FACTS_TOKEN in text:
        report.flags.append(
            Flag(
                kind="insufficient_facts",
                span=INSUFFICIENT_FACTS_TOKEN,
                reason=(
                    "The generator reported that it lacked verified facts to answer honestly. "
                    "Add the missing fact or answer this one yourself."
                ),
            )
        )

    # --- links: must be echoed from the profile, never invented ---------
    for url in _URL_RE.findall(text):
        if not _url_is_known(_url_key(url), index.url_keys):
            add(
                "unverified_link",
                url,
                "This URL is not in your profile links or verified facts.",
            )
    # Scheme-less links. Emails and full URLs are removed first so neither is
    # reported twice.
    scrubbed = _URL_RE.sub(" ", _EMAIL_RE.sub(" ", text))
    for match in _BARE_URL_RE.finditer(scrubbed):
        candidate = match.group(0)
        if not _url_is_known(_url_key(candidate), index.url_keys):
            add(
                "unverified_link",
                candidate,
                "This link is not in your profile links or verified facts.",
            )

    # --- employers -------------------------------------------------------
    target_key = normalize_company(target_company)
    for candidate in _employer_candidates(text):
        key = normalize_company(candidate)
        if not key or key in _COMMON_WORDS or len(key) < 3:
            continue
        # "the former CTO" names a role, not an employer.
        if all(_TITLE_WORD_RE.fullmatch(token) or token in _COMMON_WORDS for token in key.split()):
            continue
        if key in index.organizations:
            continue
        if allow_target_company and target_key and key == target_key:
            continue
        if known(candidate):
            continue
        add(
            "unverified_employer",
            candidate,
            f"'{candidate}' is presented as somewhere you worked or studied, but no "
            "verified fact names it.",
        )

    # --- job titles ------------------------------------------------------
    # The posting's own title is not a claim about the candidate, so it is
    # allowed; anything else has to be built from words a verified fact uses.
    wanted_title = fold(target_title)
    for match in _TITLE_CLAIM_RE.finditer(text):
        claim = match.group(1).strip()
        if known(claim):
            continue
        tokens = [t for t in fold(claim).split() if len(t) > 2]
        if tokens and all(
            t in index.folded_corpus
            or (wanted_title and t in wanted_title)
            or (allow_profile_fields and t in index.folded_profile)
            for t in tokens
        ):
            continue
        add(
            "unverified_title",
            claim,
            f"'{claim}' is claimed as a role you hold or held, but no verified fact "
            "uses that title.",
        )

    # --- credentials -----------------------------------------------------
    for match in _CREDENTIAL_RE.finditer(text):
        claim = match.group(0)
        if fold(claim) in index.credentials or known(claim):
            continue
        add(
            "unverified_credential",
            claim,
            f"'{claim}' is claimed as a qualification but is not backed by a verified "
            "education or certification fact.",
        )

    # --- dates -----------------------------------------------------------
    # Blocking, not advisory: a date nobody verified is a rewritten work
    # history, which is exactly what must never reach a submitted application.
    for year in dict.fromkeys(_YEAR_RE.findall(text)):
        if year in index.years or (allow_profile_fields and index.profile_mentions(year)):
            continue
        add(
            "unverified_date",
            year,
            f"The year {year} does not appear in any verified fact.",
        )

    # --- quantified claims ----------------------------------------------
    for match in _NUMBER_RE.finditer(text):
        raw = match.group(0)
        if _YEAR_RE.fullmatch(raw):
            continue  # reported by the date rule above
        if index.knows_number(raw):
            continue
        unit = _UNIT_RE.match(text, match.end())
        claim = raw + (unit.group(0) if unit else "")
        if known(claim.strip()):
            continue
        add(
            "unverified_metric",
            claim.strip(),
            f"The figure '{claim.strip()}' is not present in any verified fact. Metrics "
            "must be copied from something you confirmed, not estimated.",
        )
    for match in _WORDED_METRIC_RE.finditer(text):
        claim = _clause_at(text, match.start(), match.end())
        if known(match.group(0)) or known(claim):
            continue
        add(
            "unverified_metric",
            claim,
            f"'{match.group(0)}' is a quantified claim with no verified fact behind it.",
        )

    # --- work authorization / visa ---------------------------------------
    for match in _WORK_AUTH_RE.finditer(text):
        claim = _clause_at(text, match.start(), match.end())
        if allow_sourced_claims and index.has_work_authorization_fact and index.mentions(claim):
            continue
        add(
            "unverified_work_authorization",
            claim,
            "Work-authorization and visa statements must come from your explicit "
            "profile field, never from generated prose.",
        )

    # --- salary history ---------------------------------------------------
    for match in _SALARY_CLAIM_RE.finditer(text):
        claim = _clause_at(text, match.start(), match.end())
        add(
            "unverified_salary_claim",
            claim,
            "Pay history and current compensation are never generated. Remove the "
            "claim or answer the question yourself.",
        )

    # --- references -------------------------------------------------------
    for match in _REFERENCE_RE.finditer(text):
        claim = _clause_at(text, match.start(), match.end())
        if _REFERENCE_BOILERPLATE_RE.search(claim):
            continue
        if any(
            index.mentions(f.value) and fold(f.value) in fold(claim)
            for f in index.reference_facts
            if f.value
        ):
            continue
        add(
            "unverified_reference",
            claim,
            "References are never named or offered on your behalf. Share them yourself, each time.",
        )

    return report


def check_answer(
    answer: str,
    index: FactIndex,
    *,
    source_fact_id=None,
    source_field: str = "",
    question: str = "",
) -> GuardReport:
    """Screening answers must trace to a verified fact or an explicit profile field."""
    report = check(
        answer,
        index,
        allow_profile_fields=True,
        allow_sourced_claims=source_fact_id is not None,
    )
    if source_fact_id is None and not source_field and (answer or "").strip():
        report.flags.append(
            Flag(
                kind="unsourced_answer",
                span=(question or answer)[:120],
                reason=(
                    "No verified career fact or explicit profile field is linked to this "
                    "answer. Auto-submission requires every answer to be traceable to one."
                ),
            )
        )
    return report


def summarise(report: GuardReport) -> str:
    if not report.flags:
        return "All claims trace back to verified facts."
    blocking = [f for f in report.flags if f.severity == SEVERITY_BLOCK]
    parts = [f"{len(report.flags)} flag(s), {len(blocking)} blocking."]
    parts += [f"[{f.severity}] {f.kind}: '{f.span}' -- {f.reason}" for f in report.flags[:12]]
    return "\n".join(parts)
