"""The critic notices what a document left out. It must never add anything."""

from __future__ import annotations

from app.services import document_critic
from tests.conftest import make_job


def _profile(**overrides):
    from app.models.profile import CandidateProfile

    defaults = dict(
        full_name="Dana Reed",
        contact_email="dana@example.com",
        skills=["python", "sql", "pandas"],
        target_titles=["Data Analyst"],
    )
    defaults.update(overrides)
    return CandidateProfile(**defaults)


def _fact(**overrides):
    from app.models.profile import CareerFact

    defaults = dict(
        category="employment",
        key="analyst",
        value="Built reporting pipelines",
        organization="Acme",
        title="Data Analyst",
        tags=["python", "sql"],
        highlights=["Built dashboards"],
        verified=True,
    )
    defaults.update(overrides)
    return CareerFact(**defaults)


RESUME = """# Dana Reed
dana@example.com

## Experience
Data Analyst at Acme
- Built reporting pipelines in Python
"""


def test_a_verified_skill_the_document_omits_is_the_headline_finding():
    job = make_job(title="Data Analyst", extracted_skills=["python", "sql"])
    report = document_critic.critique(
        RESUME, job=job, profile=_profile(), facts=[_fact()], kind="resume"
    )
    omitted = [f for f in report.findings if f.kind == "verified_skill_omitted"]
    assert [f.suggestion for f in omitted] == ["sql"]
    assert omitted[0].severity == document_critic.SEVERITY_HIGH
    assert "sql" in omitted[0].detail


def test_a_profile_only_skill_is_reported_as_unevidenced_not_as_a_drafting_bug():
    """profile.skills ranks jobs; it is not document source material.

    Blaming the generator for omitting a self-declared skill would be blaming it
    for enforcing the no-fabrication guarantee. The actionable advice is to
    verify a fact, so the finding says that instead.
    """
    job = make_job(title="Data Analyst", extracted_skills=["terraform"])
    report = document_critic.critique(
        RESUME,
        job=job,
        profile=_profile(skills=["terraform"]),
        facts=[_fact()],  # tags are python/sql -- nothing evidences terraform
        kind="resume",
    )
    findings = [f for f in report.findings if f.suggestion == "terraform"]
    assert [f.kind for f in findings] == ["skill_claimed_but_not_evidenced"]
    assert findings[0].severity == document_critic.SEVERITY_MEDIUM
    assert "Verify a fact" in findings[0].detail
    assert not [f for f in report.findings if f.kind == "verified_skill_omitted"]


def test_a_verified_fact_outranks_the_profile_list_for_the_same_skill():
    job = make_job(title="Data Analyst", extracted_skills=["sql"])
    report = document_critic.critique(
        RESUME, job=job, profile=_profile(skills=["sql"]), facts=[_fact()], kind="resume"
    )
    kinds = [f.kind for f in report.findings if f.suggestion == "sql"]
    assert kinds == ["verified_skill_omitted"]


def test_a_skill_the_document_already_mentions_is_not_flagged():
    job = make_job(title="Data Analyst", extracted_skills=["python"])
    report = document_critic.critique(
        RESUME, job=job, profile=_profile(), facts=[_fact()], kind="resume"
    )
    assert "python" in report.matched_keywords
    assert not [f for f in report.findings if f.suggestion == "python"]


def test_a_skill_the_candidate_cannot_evidence_is_never_suggested():
    """The critic may not invent qualifications any more than the drafter may.

    Kubernetes is in the posting and absent from the resume, but the candidate
    has neither a profile skill nor a verified fact for it -- so there is
    nothing truthful to suggest, and the critic stays silent about it.
    """
    job = make_job(title="Data Analyst", extracted_skills=["python", "kubernetes"])
    report = document_critic.critique(
        RESUME, job=job, profile=_profile(), facts=[_fact()], kind="resume"
    )
    assert "kubernetes" in report.missing_keywords
    assert not [f for f in report.findings if f.suggestion == "kubernetes"]


def test_every_suggestion_is_a_string_the_candidate_already_owns():
    """Structural guarantee, checked across a wide posting."""
    job = make_job(
        title="Data Analyst",
        extracted_skills=["python", "sql", "pandas", "kubernetes", "rust", "terraform"],
    )
    profile = _profile()
    facts = [_fact()]
    report = document_critic.critique(RESUME, job=job, profile=profile, facts=facts, kind="resume")
    owned = {s.lower() for s in profile.skills}
    for fact in facts:
        owned |= {t.lower() for t in fact.tags}
    for finding in report.findings:
        if finding.suggestion:
            assert finding.suggestion.lower() in owned, finding.suggestion


def test_unverified_facts_do_not_license_a_suggestion():
    job = make_job(title="Data Analyst", extracted_skills=["terraform"])
    unverified = _fact(tags=["terraform"], verified=False)
    report = document_critic.critique(
        RESUME, job=job, profile=_profile(skills=[]), facts=[unverified], kind="resume"
    )
    assert not [f for f in report.findings if f.suggestion == "terraform"]


def test_coverage_is_reported_and_low_coverage_is_flagged():
    job = make_job(
        title="Data Analyst",
        extracted_skills=["python", "rust", "go", "scala", "terraform"],
    )
    report = document_critic.critique(
        RESUME, job=job, profile=_profile(), facts=[_fact()], kind="resume"
    )
    assert report.keyword_coverage == 0.2
    assert any(f.kind == "low_keyword_coverage" for f in report.findings)


def test_a_posting_with_no_extractable_skills_is_full_coverage_not_zero():
    """Nothing to match against is not the document's fault."""
    job = make_job(title="Data Analyst", extracted_skills=[])
    report = document_critic.critique(
        RESUME, job=job, profile=_profile(), facts=[_fact()], kind="resume"
    )
    assert report.keyword_coverage == 1.0
    assert not any(f.kind == "low_keyword_coverage" for f in report.findings)


def test_a_missing_contact_email_is_flagged_on_a_resume():
    job = make_job(title="Data Analyst", extracted_skills=["python"])
    body = "## Experience\nData Analyst at Acme\n- Built pipelines in Python\n"
    report = document_critic.critique(
        body, job=job, profile=_profile(), facts=[_fact()], kind="resume"
    )
    contact = [f for f in report.findings if f.kind == "missing_contact"]
    assert contact and contact[0].severity == document_critic.SEVERITY_HIGH


def test_a_title_the_document_never_echoes_is_flagged():
    job = make_job(title="Machine Learning Engineer", extracted_skills=["python"])
    report = document_critic.critique(
        RESUME, job=job, profile=_profile(), facts=[_fact()], kind="resume"
    )
    assert any(f.kind == "title_not_echoed" for f in report.findings)


def test_a_short_resume_is_flagged():
    job = make_job(title="Data Analyst", extracted_skills=["python"])
    report = document_critic.critique(
        "dana@example.com Data Analyst Python",
        job=job,
        profile=_profile(),
        facts=[_fact()],
        kind="resume",
    )
    assert any(f.kind == "too_short" for f in report.findings)


def test_length_findings_are_resume_only():
    """A short cover letter is a short cover letter, not a defect."""
    job = make_job(title="Data Analyst", extracted_skills=["python"])
    report = document_critic.critique(
        "Dear team, I am Dana. dana@example.com Python. Data Analyst.",
        job=job,
        profile=_profile(),
        facts=[_fact()],
        kind="cover letter",
    )
    assert not any(f.kind in {"too_short", "too_long"} for f in report.findings)


def test_the_report_serialises_for_storage():
    job = make_job(title="Data Analyst", extracted_skills=["python", "sql"])
    payload = document_critic.critique(
        RESUME, job=job, profile=_profile(), facts=[_fact()], kind="resume"
    ).as_dict()

    assert set(payload) == {
        "score",
        "keyword_coverage",
        "matched_keywords",
        "missing_keywords",
        "word_count",
        "findings",
    }
    assert isinstance(payload["score"], int)
    assert 0 <= payload["score"] <= 100
    import json

    json.dumps(payload)  # must survive the JSON column


def test_score_stays_in_range_when_findings_pile_up():
    job = make_job(
        title="Machine Learning Engineer",
        extracted_skills=["python", "sql", "pandas", "rust", "go", "scala"],
    )
    report = document_critic.critique(
        "x", job=job, profile=_profile(), facts=[_fact()], kind="resume"
    )
    assert 0 <= report.score <= 100
