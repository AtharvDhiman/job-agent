"""Autopilot: run the whole pipeline in one call and say what still blocks it.

After a resume upload the agent can discover, score and draft on its own. What
it can NEVER do on its own is verify a fact, enable a source, or authorize a
platform -- those stay with the human, and gates()/next_steps() exist to tell
them exactly which of those switches are still off. run_pipeline() only chains
services that are individually safe: drafting always ends at the policy gate in
services/policy.py, so a run with nothing authorized produces review tasks, not
submissions.
"""

from __future__ import annotations

import re
from datetime import UTC, date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import (
    ApplicationStatus,
    AuditAction,
    DocumentKind,
    FactCategory,
    MatchDecision,
    Seniority,
)
from app.core.logging import get_logger
from app.models.application import Application
from app.models.job import Job, JobMatch, JobSourceSubscription
from app.models.profile import CandidateProfile, CareerFact, Document
from app.models.user import AgentSettings, PlatformAuthorization, User
from app.services import application_workflow as workflow
from app.services import audit, discovery, matching, taxonomy
from app.services.resume_parser import ParsedResume, ProposedFact

log = get_logger(__name__)

#: One run drafts at most this many applications, mirroring the worker's
#: per-user cap so an on-demand run cannot out-produce the background one.
MAX_DRAFTS_PER_RUN = 10


def _ensure_url(value: str) -> str:
    # The parser accepts scheme-less links ("linkedin.com/in/x"); the profile
    # schemas only accept absolute http(s) URLs, so normalise before storing.
    if value and not value.startswith(("http://", "https://")):
        return f"https://{value}"
    return value


def _recent_employment(proposed_facts: list[ProposedFact]) -> list[ProposedFact]:
    employment = [f for f in proposed_facts if f.category == FactCategory.EMPLOYMENT.value]
    # Current role first, then most recently started: the ordering a recruiter
    # would read off the resume itself.
    return sorted(employment, key=lambda f: (f.is_current, f.start_date or date.min), reverse=True)


#: Seed scaffolding like "[SKILL 1]" or "[YOUR NAME]". A bracketed placeholder is
#: an instruction to the human, not a statement by them, so for the purposes of
#: "never overwrite what the human typed" it counts as empty.
_PLACEHOLDER_RE = re.compile(r"^\[[^\[\]]*\]$")


def _is_blank(value: str | None) -> bool:
    if not value:
        return True
    return bool(_PLACEHOLDER_RE.match(value.strip()))


def _is_blank_list(values: list | None) -> bool:
    if not values:
        return True
    return all(_is_blank(str(item)) for item in values)


def configure_from_resume(
    db: Session,
    profile: CandidateProfile,
    parsed: ParsedResume,
    proposed_facts: list[ProposedFact],
) -> dict[str, object]:
    """Fill EMPTY profile fields from a parsed resume. Never overwrite.

    The profile is the human's own statement; the parser is a guess. A guess may
    only ever land where the human has said nothing at all.

    Deliberately never filled, even when the resume mentions them:
    years_experience, min_salary_amount, requires_sponsorship,
    work_authorization and preferred_countries. Those drive hard filters and
    truthful screening answers, so a parser misread there would silently
    misrepresent the candidate; they must be typed in by the human.
    """
    filled: dict[str, object] = {}

    if _is_blank_list(profile.skills) and parsed.skills:
        profile.skills = list(parsed.skills)
        filled["skills"] = profile.skills

    employment = _recent_employment(proposed_facts)
    if _is_blank_list(profile.target_titles) and employment:
        titles: list[str] = []
        for fact in employment:
            title = fact.title.strip()
            if title and title not in titles:
                titles.append(title)
        if titles:
            profile.target_titles = titles[:5]
            filled["target_titles"] = profile.target_titles

    if _is_blank(profile.linkedin_url) and parsed.linkedin:
        profile.linkedin_url = _ensure_url(parsed.linkedin)
        filled["linkedin_url"] = profile.linkedin_url

    if _is_blank_list(profile.portfolio_urls) and parsed.github:
        profile.portfolio_urls = [_ensure_url(parsed.github)]
        filled["portfolio_urls"] = profile.portfolio_urls

    if _is_blank(profile.phone) and parsed.phones:
        profile.phone = parsed.phones[0]
        filled["phone"] = profile.phone

    if profile.seniority_level == Seniority.UNKNOWN.value and employment:
        inferred = taxonomy.infer_seniority(employment[0].title)
        if inferred != Seniority.UNKNOWN:
            profile.seniority_level = inferred.value
            filled["seniority_level"] = profile.seniority_level

    if filled:
        db.flush()
        audit.record(
            db,
            AuditAction.PROFILE_UPDATED,
            user_id=profile.user_id,
            object_type="candidate_profile",
            object_id=str(profile.id),
            payload={"source": "resume_autoconfigure", "fields": sorted(filled)},
        )
    return filled


def gates(db: Session, user: User, agent_settings: AgentSettings) -> dict:
    """The readiness checklist: every switch that must be on for full autopilot."""
    profile = db.execute(
        select(CandidateProfile).where(CandidateProfile.user_id == user.id)
    ).scalar_one_or_none()

    verified = unverified = 0
    if profile is not None:
        rows = db.execute(
            select(CareerFact.verified, func.count(CareerFact.id))
            .where(CareerFact.profile_id == profile.id)
            .group_by(CareerFact.verified)
        ).all()
        for is_verified, count in rows:
            if is_verified:
                verified = int(count)
            else:
                unverified = int(count)

    authorized = [
        row.platform_key
        for row in db.execute(
            select(PlatformAuthorization).where(PlatformAuthorization.user_id == user.id)
        ).scalars()
        if row.is_active
    ]
    enabled_sources = db.execute(
        select(func.count(JobSourceSubscription.id)).where(
            JobSourceSubscription.user_id == user.id,
            JobSourceSubscription.enabled.is_(True),
        )
    ).scalar_one()
    resume_uploaded = (
        db.execute(
            select(Document.id)
            .where(
                Document.user_id == user.id,
                Document.kind == DocumentKind.RESUME_SOURCE.value,
            )
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )
    queued = db.execute(
        select(func.count(Application.id)).where(
            Application.user_id == user.id,
            Application.status.in_(
                (ApplicationStatus.QUEUED.value, ApplicationStatus.APPROVED.value)
            ),
        )
    ).scalar_one()

    return {
        "automation_enabled": agent_settings.automation_enabled,
        "global_automation_enabled": settings.automation_global_enabled,
        "authorized_platforms": sorted(authorized),
        "verified_fact_count": verified,
        "unverified_fact_count": unverified,
        "enabled_source_count": int(enabled_sources),
        "resume_uploaded": resume_uploaded,
        "applications_today": workflow.applications_today(db, user.id),
        "daily_application_limit": agent_settings.daily_application_limit,
        "queued_application_count": int(queued),
    }


def next_steps(gate_state: dict) -> list[str]:
    """The remaining manual steps, in the order they should be done."""
    steps: list[str] = []
    if not gate_state["resume_uploaded"]:
        steps.append("Upload a resume")
    if gate_state["unverified_fact_count"] > 0:
        steps.append(
            f"Verify the {gate_state['unverified_fact_count']} proposed career facts under Profile"
        )
    if gate_state["enabled_source_count"] == 0:
        steps.append("Enable at least one job source under Settings")
    if not gate_state["automation_enabled"]:
        steps.append("Resume automation (it is paused)")
    if not gate_state["global_automation_enabled"]:
        steps.append("Ask the operator to set AUTOMATION_GLOBAL_ENABLED=true on the server")
    if not gate_state["authorized_platforms"]:
        steps.append("Authorize at least one platform for auto-submit under Settings")
    if gate_state["queued_application_count"] > 0:
        steps.append(
            "Start the local browser assistant; it performs the actual submissions on your machine"
        )
    return steps


def _agent_settings(db: Session, user: User) -> AgentSettings:
    row = db.execute(
        select(AgentSettings).where(AgentSettings.user_id == user.id)
    ).scalar_one_or_none()
    if row is None:
        # Same defaults as api.deps.get_agent_settings; workers reach this
        # function without going through the dependency.
        row = AgentSettings(
            user_id=user.id,
            auto_submit_min_score=settings.auto_submit_min_score,
            daily_application_limit=settings.daily_application_limit,
            job_max_age_hours=settings.job_max_age_hours,
            discovery_interval_minutes=settings.discovery_interval_minutes,
        )
        db.add(row)
        db.flush()
    return row


def _scores_are_stale(db: Session, user: User) -> bool:
    """True when the profile or a verified fact changed after the newest match."""
    newest_match = db.execute(
        select(func.max(JobMatch.created_at)).where(JobMatch.user_id == user.id)
    ).scalar_one_or_none()
    if newest_match is None:
        return False  # nothing scored yet; nothing to invalidate

    profile = db.execute(
        select(CandidateProfile).where(CandidateProfile.user_id == user.id)
    ).scalar_one_or_none()
    if profile is None:
        return False

    newest_fact_change = db.execute(
        select(func.max(CareerFact.updated_at)).where(CareerFact.profile_id == profile.id)
    ).scalar_one_or_none()

    def _aware(value):
        # SQLite hands back naive datetimes for server-default columns while the
        # ORM writes aware ones; both mean UTC here, so pin the zone explicitly
        # or the comparison below raises on the mix.
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    candidates = [_aware(profile.updated_at), _aware(newest_fact_change)]
    latest_input_change = max((c for c in candidates if c is not None), default=None)
    return latest_input_change is not None and latest_input_change > _aware(newest_match)


def run_pipeline(db: Session, user: User, *, include_drafting: bool = True) -> dict:
    """Discover -> score -> draft in one pass, then report what still blocks.

    Drafting is always safe to include: every draft ends at policy.decide(), so
    a below-threshold score or a missing authorization becomes a review task,
    never a submission. This function chains services; it decides nothing.
    """
    agent_settings = _agent_settings(db, user)

    # A score is a comparison between a job and the profile AS IT WAS when the
    # match row was written. If the profile or its verified facts changed since
    # the newest match, every existing score describes a person who no longer
    # exists on file - so they are discarded and rebuilt, exactly as the manual
    # /matches/rescore endpoint does. Without this, upload -> verify -> run
    # would keep ranking against the pre-upload profile and "hands-off" would
    # quietly mean "hands-off with stale data".
    if _scores_are_stale(db, user):
        db.execute(JobMatch.__table__.delete().where(JobMatch.user_id == user.id))
        db.flush()

    stats = discovery.run_all_for_user(db, user.id)
    discovery_block = {
        "sources_run": len(stats),
        "created": sum(s.created for s in stats),
        "updated": sum(s.updated for s in stats),
        "duplicates": sum(s.duplicates for s in stats),
        "blocked": [
            {"connector_key": s.connector_key, "identifier": s.identifier, "error": s.error}
            for s in stats
            if s.status == "blocked_by_policy"
        ],
    }

    scored = matching.score_for_user(db, user)
    scoring_block = {
        "scored": scored.get("scored", 0),
        "shortlisted": scored.get("shortlisted", 0),
        "rejected": scored.get("rejected", 0),
    }

    drafted = queued = review = 0
    if include_drafting:
        existing = select(Application.job_id).where(Application.user_id == user.id)
        matches = list(
            db.execute(
                select(JobMatch)
                .where(
                    JobMatch.user_id == user.id,
                    JobMatch.decision == MatchDecision.SHORTLISTED.value,
                    JobMatch.dismissed_at.is_(None),
                    JobMatch.job_id.not_in(existing),
                )
                .order_by(JobMatch.score.desc())
                .limit(MAX_DRAFTS_PER_RUN)
            ).scalars()
        )
        for match in matches:
            job = db.get(Job, match.job_id)
            if job is None:
                continue
            result = workflow.draft_application(db, user, job, match)
            drafted += 1
            if result.decision.may_submit:
                queued += 1
            else:
                review += 1
            db.flush()

    gate_state = gates(db, user, agent_settings)
    log.info(
        "autopilot.run",
        user_id=str(user.id),
        discovered=discovery_block["created"],
        scored=scoring_block["scored"],
        drafted=drafted,
    )
    return {
        "discovery": discovery_block,
        "scoring": scoring_block,
        "drafting": {
            "drafted": drafted,
            "queued_for_auto_submit": queued,
            "sent_to_review": review,
        },
        "gates": gate_state,
        "next_steps": next_steps(gate_state),
    }
