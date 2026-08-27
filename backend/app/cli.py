"""Terminal front door to the engine the web app already drives.

One engine, two front doors. Every subcommand here opens the SAME database the
API opens and calls the SAME services -- discovery, matching, the drafting
workflow, the policy gate -- so a job scraped from a terminal appears on the
dashboard, and an application drafted here lands in the same review queue.
Nothing in this module decides anything: it resolves a user, calls a service,
and formats what came back.

In particular it never concludes that automation is permitted. `apply` asks
services/policy.decide() before it drafts and refuses outright on a platform
whose terms forbid automated applying, then obeys the second, authoritative
decision the drafting workflow makes for itself.

Run:  python -m app.cli status
      python -m app.cli scrape --limit 10 --min-score 70
      python -m app.cli verify-facts
      python -m app.cli add-portal "Northwind Systems"
      python -m app.cli apply --job-id <uuid>
      python -m app.cli report --hours 168 --out dashboard.html
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import agent_settings_or_default, get_agent_settings

# Calling the route functions rather than copying their bodies is the point of
# this module: `status` must show the same six buckets the dashboard shows, and
# `apply --url` must file a pasted link through the same attribution and scoring
# path as the web app's quick-add. A FastAPI route is an ordinary function; its
# Annotated parameters only mean something to the dependency injector.
from app.api.v1.dashboard import dashboard as dashboard_view
from app.api.v1.jobs import create_source, quick_add_job, rescore
from app.api.v1.profile import list_facts
from app.api.v1.profile import verify_facts as verify_facts_route
from app.api.v1.source_tools import find_company_boards, source_catalog
from app.core.config import settings
from app.core.enums import Seniority, SubmissionPolicy
from app.core.logging import configure_logging
from app.core.security import Role, role_allows
from app.db.session import session_scope
from app.models.job import Job, JobMatch, JobSourceSubscription
from app.models.profile import CandidateProfile, CareerFact
from app.models.user import AgentSettings, PlatformAuthorization, User
from app.schemas.jobs import QuickAddIn, SubscriptionIn
from app.schemas.profile import VerifyFactsIn
from app.schemas.settings import DashboardOut
from app.schemas.source_tools import BoardSearchIn
from app.services import application_workflow as workflow
from app.services import (
    autopilot,
    discovery,
    html_report,
    matching,
    pipeline,
    policy,
    portal_status,
    storage,
)
from app.utils.text import truncate

EXIT_OK = 0
EXIT_ERROR = 2

#: The profile fields that actually change what the agent does: they feed the
#: hard filters, the ranking weights and the screening answers. Fields that only
#: decorate a document are deliberately left out, so "80% complete" means
#: "80% of the answers the engine reads", not "80% of the form".
_PROFILE_FIELDS: tuple[tuple[str, str], ...] = (
    ("full_name", "name"),
    ("contact_email", "contact email"),
    ("location_country", "country"),
    ("target_titles", "target titles"),
    ("skills", "skills"),
    ("seniority_level", "seniority"),
    ("employment_types", "employment types"),
    ("work_arrangement_preference", "work arrangement"),
    ("preferred_countries", "preferred countries"),
    ("years_experience", "years of experience"),
    ("min_salary_amount", "minimum salary"),
    ("willing_to_relocate", "relocation answer"),
    ("requires_sponsorship", "sponsorship answer"),
    ("work_authorization", "work authorization"),
)


class CliError(Exception):
    """A message for the person at the keyboard.

    Raised instead of letting an exception escape, because a traceback is not an
    error message. dispatch() prints the text and returns a non-zero exit code.
    """


# --------------------------------------------------------------------- output
def _heading(text: str) -> None:
    print()
    print(text)
    print("-" * len(text))


def _table(headers: Sequence[str], rows: Sequence[Sequence[object]], aligns: str = "") -> None:
    """Fixed-width columns, ASCII only.

    No ANSI colour anywhere in this module: Windows terminals disagree about
    whether they render escape codes, and a status report full of literal
    "[32m" is worse than a plain one.
    """
    cells = [[str(cell) for cell in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in cells:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    aligns = (aligns + "l" * len(headers))[: len(headers)]

    def render(row: Sequence[str]) -> str:
        parts = [
            cell.rjust(width) if align == "r" else cell.ljust(width)
            for cell, width, align in zip(row, widths, aligns, strict=False)
        ]
        return "  ".join(parts).rstrip()

    print(render(headers))
    print(render(["-" * width for width in widths]))
    for row in cells:
        print(render(row))


def _when(value: datetime | None) -> str:
    """SQLite hands back naive datetimes where the ORM wrote aware ones."""
    if value is None:
        return "never"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _database_label() -> str:
    """Which database this is, without its password.

    Worth printing: the whole promise of this CLI is that it drives the same
    database as the web UI, and the only way to check that is to see the URL.
    """
    url = settings.database_url
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    return f"{scheme}://***@{rest.rpartition('@')[2]}"


def _storage_root() -> str:
    """Where generated documents land, or "" when the backend has no local path.

    S3Storage has no filesystem root, and inventing one would be a path that
    does not exist.
    """
    root = getattr(storage.get_storage(), "root", None)
    return str(Path(root).resolve()) if root is not None else ""


def _relative_key(storage_key: str) -> str:
    """Trim the leading user-id segment of a storage key.

    Every row of a document table belongs to the same user, so that segment is
    36 identical characters of noise in the widest column.
    """
    _, _, rest = storage_key.partition("/")
    return rest or storage_key


# ---------------------------------------------------------------- user lookup
def resolve_user(db: Session, email: str = "") -> User:
    """The account to act as: the one named, or the sole owner.

    --email is not a way around the API's checks. Every subcommand here hands
    the resolved User to code the web app guards with RequireOperator, and
    deps.get_current_user refuses a deactivated account before any of it runs,
    so the same two conditions are enforced on both branches: the account must
    be active, and it must sit at OPERATOR or above. Without this, `--email
    <viewer>` quick-added jobs, re-scored and drafted applications through
    paths the API answers with 401 or 403.
    """
    if email:
        wanted = email.strip().lower()
        user = db.execute(select(User).where(func.lower(User.email) == wanted)).scalar_one_or_none()
        if user is None:
            known = [row.email for row in db.execute(select(User).limit(10)).scalars()]
            hint = f" Known accounts: {', '.join(known)}." if known else ""
            raise CliError(f"No account found for {email}.{hint}")
        if not user.is_active:
            raise CliError(f"{user.email} is deactivated. The API refuses it too; reactivate it.")
        if not role_allows(user.role, Role.OPERATOR):
            raise CliError(
                f"{user.email} has the '{user.role}' role. This command needs "
                f"'{Role.OPERATOR.value}' or higher, exactly as the API requires."
            )
        return user

    owners = list(
        db.execute(
            select(User).where(User.role == Role.OWNER.value, User.is_active.is_(True))
        ).scalars()
    )
    if len(owners) == 1:
        return owners[0]
    if not owners:
        raise CliError(
            "No owner account exists yet. Run `python -m seed.seed` first, or pass --email."
        )
    raise CliError(
        "More than one owner account exists; choose one with --email: "
        + ", ".join(sorted(owner.email for owner in owners))
    )


def _require_profile(db: Session, user: User) -> CandidateProfile:
    profile = db.execute(
        select(CandidateProfile).where(CandidateProfile.user_id == user.id)
    ).scalar_one_or_none()
    if profile is None:
        raise CliError(
            f"{user.email} has no candidate profile yet. Create one in the web app "
            "(Settings -> Profile) or run `python -m seed.seed`."
        )
    return profile


# ------------------------------------------------------------------ reporting
def _is_filled(value: object) -> bool:
    """Is this profile field actually answered?

    A seeded profile is full of "[PLACEHOLDER]" strings and `unknown` defaults.
    autopilot already decides what counts as blank when it refuses to overwrite
    a human's answer; measuring completeness by a different rule would report a
    freshly seeded profile as finished.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        # Tri-state on purpose: False is an answer, only None means "not told".
        return True
    if isinstance(value, str):
        return not autopilot._is_blank(value) and value != Seniority.UNKNOWN.value
    if isinstance(value, int | float | Decimal):
        # Zero years of experience is an answer. Decimal belongs here because
        # years_experience is Numeric(4, 1), so SQLAlchemy hands back
        # Decimal('0.0') -- which is neither int nor float and is falsy, so
        # without it a graduate was told to fill in a field they had filled.
        return True
    if isinstance(value, list):
        return not autopilot._is_blank_list(value)
    return bool(value)


def _profile_block(db: Session, user: User, gate_state: dict) -> None:
    _heading("Profile")
    profile = db.execute(
        select(CandidateProfile).where(CandidateProfile.user_id == user.id)
    ).scalar_one_or_none()
    if profile is None:
        print("No candidate profile yet. Nothing can be scored or drafted until there is one.")
        return

    missing = [label for field, label in _PROFILE_FIELDS if not _is_filled(getattr(profile, field))]
    filled = len(_PROFILE_FIELDS) - len(missing)
    percent = round(filled * 100 / len(_PROFILE_FIELDS))
    print(f"Completeness    : {filled}/{len(_PROFILE_FIELDS)} fields ({percent}%)")
    if missing:
        print(f"Still needed    : {', '.join(missing)}")

    verified = gate_state["verified_fact_count"]
    total = verified + gate_state["unverified_fact_count"]
    print(f"Verified facts  : {verified} of {total}")
    print(f"Resume uploaded : {_yes_no(gate_state['resume_uploaded'])}")
    if verified == 0:
        print("Nothing can be written into a document until at least one fact is verified.")


def _sources_block(db: Session, user: User) -> None:
    _heading("Sources")
    subscriptions = list(
        db.execute(
            select(JobSourceSubscription)
            .where(JobSourceSubscription.user_id == user.id)
            .order_by(JobSourceSubscription.connector_key, JobSourceSubscription.identifier)
        ).scalars()
    )
    if not subscriptions:
        print("No sources configured. Discovery has nothing to poll.")
        return

    enabled = sum(1 for row in subscriptions if row.enabled)
    print(f"{enabled} of {len(subscriptions)} enabled")
    _table(
        ["CONNECTOR", "IDENTIFIER", "ON", "LAST RUN", "STATUS", "SEEN"],
        [
            [
                row.connector_key,
                truncate(row.identifier, 30),
                _yes_no(row.enabled),
                _when(row.last_run_at),
                row.last_status,
                row.jobs_seen,
            ]
            for row in subscriptions
        ],
        aligns="lllllr",
    )
    for row in subscriptions:
        if row.last_error:
            print(f"  {row.connector_key}/{row.identifier}: {truncate(row.last_error, 90)}")


def _portals_block(db: Session, user: User, agent_settings: AgentSettings) -> None:
    _heading("Portals")
    states = portal_status.portal_states(db, user, agent_settings)
    _table(
        ["PORTAL", "STATUS", "SOURCES", "SEEN", "WHAT IS STOPPING IT"],
        [
            [
                truncate(state["display_name"], 24),
                state["status"],
                f"{state['enabled_source_count']}/{state['source_count']}",
                state["jobs_seen"],
                _first_blocker(state["blockers"]),
            ]
            for state in states
        ],
        aligns="lllrl",
    )


def _first_blocker(blockers: list[str]) -> str:
    """The nearest blocker, plus how many more are behind it.

    portal_status returns every reason a portal is not working; printing them
    all would make the row unreadable, and printing only the first would hide
    that there are others.
    """
    if not blockers:
        return "nothing"
    extra = f" (+{len(blockers) - 1} more)" if len(blockers) > 1 else ""
    return truncate(blockers[0], 44) + extra


def _buckets_block(view: DashboardOut, hours: int) -> None:
    _heading(f"Buckets (last {hours}h)")
    _table(
        ["BUCKET", "COUNT", "WHERE"],
        [
            [label, view.buckets[key]["count"], view.buckets[key]["link"]]
            for key, label, _note in pipeline.BUCKET_LABELS
        ],
        aligns="lrl",
    )
    for entry in view.buckets["failed_or_stopped"].get("failure_reasons", []):
        print(f"  stopped: {entry['label']} ({entry['count']})")


# ------------------------------------------------------------------- matching
def one_line_reason(match: JobMatch) -> str:
    """The single most useful sentence about a scored match.

    A rejection's reason is the filter it failed; a shortlist's reason is what
    it matched on. Falls back to the stored ranking explanation rather than
    composing anything new, so the terminal and the dashboard agree.
    """
    if match.hard_filter_failures:
        return "; ".join(match.hard_filter_failures)
    if match.matching_skills:
        risk = f"; risk: {match.risks[0]}" if match.risks else ""
        return "matches " + ", ".join(match.matching_skills[:6]) + risk
    for line in (match.explanation or "").splitlines()[1:]:
        if line.strip():
            return line.strip()
    return match.decision.replace("_", " ")


def _top_matches(
    db: Session, user_id: uuid.UUID, *, limit: int, min_score: int
) -> tuple[list[JobMatch], bool]:
    """The shortlist, or -- when nothing cleared the bar -- what came closest.

    An empty table would be a true but useless answer to "what did you find".
    The fallback shows the same jobs with the decision that set them aside.
    """
    shortlist = [
        match
        for match in matching.daily_shortlist(db, user_id, limit=limit)
        if match.score >= min_score
    ]
    if shortlist:
        return shortlist, False

    rows = db.execute(
        select(JobMatch)
        .where(
            JobMatch.user_id == user_id,
            JobMatch.dismissed_at.is_(None),
            JobMatch.score >= min_score,
        )
        .order_by(JobMatch.score.desc())
        .limit(limit)
    ).scalars()
    return list(rows), True


def _print_matches(db: Session, user: User, *, limit: int, min_score: int) -> None:
    matches, fallback = _top_matches(db, user.id, limit=limit, min_score=min_score)
    _heading(f"Top {limit} matches" + (f" at or above {min_score}" if min_score else ""))
    if not matches:
        # "Nothing scored yet" is only true when nothing was scored. With a
        # --min-score set it would contradict the count printed two lines above
        # and send the user to `scrape`, which cannot help: the jobs exist and
        # the filter is what hid them.
        if min_score > 0:
            print(
                f"No match scored {min_score} or higher. Lower --min-score to see what did score."
            )
        else:
            print("Nothing scored yet. Run `scrape` to discover jobs, or add a source first.")
        return
    if fallback:
        print("Nothing was shortlisted. These are the highest scores and why they were set aside.")

    headers = ["SCORE", "TITLE", "COMPANY", "WHY"]
    rows: list[list[object]] = []
    for match in matches:
        job = match.job
        row = [
            match.score,
            truncate(job.title, 34),
            truncate(job.company, 22),
            truncate(one_line_reason(match), 48),
        ]
        if fallback:
            row.insert(1, match.decision)
        rows.append(row)
    if fallback:
        headers.insert(1, "DECISION")
    _table(headers, rows, aligns="r")


# ------------------------------------------------------------------- commands
def cmd_status(db: Session, args: argparse.Namespace) -> int:
    user = resolve_user(db, args.email)
    # The read-only half of the pair: get_agent_settings INSERTs the row when
    # an account has none, and session_scope would commit it -- so the command
    # that promises to change nothing would have written the automation
    # configuration as a side effect of printing it.
    agent_settings = agent_settings_or_default(db, user)
    hours = max(1, min(720, args.hours))
    view = dashboard_view(db, user, hours=hours, agent_settings=agent_settings)

    _heading(f"Status for {user.email}")
    print(f"Database        : {_database_label()}")
    print(
        "Automation      : "
        + ("on" if view.automation_enabled else "paused")
        + (f" ({view.paused_reason})" if view.paused_reason else "")
        + ("; server switch on" if view.global_automation_enabled else "; server switch off")
    )
    print(f"Applications    : {view.applications_today} of {view.daily_application_limit} today")
    print(f"Auto-submit at  : score {view.auto_submit_min_score} or higher")
    print(f"Drafting mode   : {view.llm_mode}")

    gate_state = autopilot.gates(db, user, agent_settings)
    _profile_block(db, user, gate_state)
    _sources_block(db, user)
    _portals_block(db, user, agent_settings)
    _buckets_block(view, hours)

    steps = autopilot.next_steps(gate_state)
    _heading("Next steps")
    if not steps:
        print("Nothing is blocked. The agent can run unattended.")
    for index, step in enumerate(steps, start=1):
        print(f"{index}. {step}")
    return EXIT_OK


def cmd_scrape(db: Session, args: argparse.Namespace) -> int:
    user = resolve_user(db, args.email)
    stats = discovery.run_all_for_user(db, user.id)

    _heading("Discovery")
    if not stats:
        print("No enabled sources. Nothing was fetched.")
    else:
        _table(
            ["CONNECTOR", "IDENTIFIER", "FETCHED", "CREATED", "UPDATED", "DUPES", "STATUS"],
            [
                [
                    row.connector_key,
                    truncate(row.identifier, 30),
                    row.fetched,
                    row.created,
                    row.updated,
                    row.duplicates,
                    row.status,
                ]
                for row in stats
            ],
            aligns="llrrrrl",
        )
        for row in stats:
            if row.error:
                print(f"  {row.connector_key}/{row.identifier}: {truncate(row.error, 90)}")

    scored = matching.score_for_user(db, user)
    if scored.get("error"):
        raise CliError(
            "Jobs were fetched but nothing could be scored: this account has no profile or "
            "no agent settings yet."
        )
    _heading("Scoring")
    print(
        f"Scored {scored['scored']}, shortlisted {scored['shortlisted']}, "
        f"set aside {scored['rejected']}"
    )
    _print_matches(db, user, limit=args.limit, min_score=args.min_score)
    return EXIT_OK


def cmd_rank(db: Session, args: argparse.Namespace) -> int:
    user = resolve_user(db, args.email)
    # The same endpoint the web app's "rescore" button calls: existing matches
    # are cleared first, because a score describes the profile as it was when
    # the match was written and re-ranking against a stale one is a lie. That
    # clearing also drops any per-match dismissal the user set by hand, which
    # is why --scan-limit is a real flag and not a hard-coded 500: the size of
    # the pass is the operator's decision, not this module's.
    # Clamped to the same window the route's Query() enforces: calling a route
    # function directly skips FastAPI's validation, and the bound belongs to
    # the endpoint, not to the transport that reached it.
    scored = rescore(db, user, limit=max(1, min(2000, args.scan_limit)))
    if scored.get("error"):
        raise CliError(
            "Nothing could be scored: this account has no profile or no agent settings yet."
        )
    _heading("Scoring")
    print(
        f"Scored {scored['scored']}, shortlisted {scored['shortlisted']}, "
        f"set aside {scored['rejected']}"
    )
    _print_matches(db, user, limit=args.limit, min_score=args.min_score)
    return EXIT_OK


def _job_from_url(db: Session, user: User, args: argparse.Namespace) -> Job:
    """File a pasted job through the web app's own quick-add route.

    Not reimplemented here: quick-add is where a URL's host is turned into a
    connector key, and therefore where a Naukri link inherits Naukri's
    PROHIBITED policy instead of the generic manual default.
    """
    for flag, value in (("--company", args.company), ("--title", args.title)):
        if not value:
            raise CliError(f"{flag} is required with --url.")
    if not args.description_file:
        raise CliError("--description-file is required with --url.")
    path = Path(args.description_file)
    try:
        description = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CliError(f"Cannot read {path}: {exc.strerror or exc}") from exc
    if not description.strip():
        raise CliError(f"{path} is empty. Paste the job description text into it first.")

    payload = QuickAddIn(
        url=args.url,
        company=args.company,
        title=args.title,
        description_text=description,
        location_raw=args.location,
        draft=False,  # drafting happens below, so this command can report on it
    )
    added = quick_add_job(payload, db, user)
    print(f"Added {added.title} at {added.company} (job {added.job_id})")
    job = db.get(Job, added.job_id)
    if job is None:  # pragma: no cover - quick-add just flushed it
        raise CliError("The job was added but could not be read back.")
    return job


def _preflight(
    db: Session, user: User, job: Job, agent_settings: AgentSettings, score: int
) -> policy.PolicyDecision:
    """Ask the one authority before doing any work on this job."""
    authorization = db.execute(
        select(PlatformAuthorization).where(
            PlatformAuthorization.user_id == user.id,
            PlatformAuthorization.platform_key == job.connector_key,
        )
    ).scalar_one_or_none()
    return policy.decide(
        job=job,
        connector_policy=job.submission_policy_default,
        authorization=authorization,
        agent_settings=agent_settings,
        score=score,
        global_enabled=settings.automation_global_enabled,
        applications_today=workflow.applications_today(db, user.id),
    )


def cmd_apply(db: Session, args: argparse.Namespace) -> int:
    user = resolve_user(db, args.email)
    _require_profile(db, user)
    agent_settings = get_agent_settings(db, user)

    if bool(args.job_id) == bool(args.url):
        raise CliError("Pass exactly one of --job-id or --url.")

    if args.url:
        job = _job_from_url(db, user, args)
    else:
        try:
            job_uuid = uuid.UUID(args.job_id)
        except ValueError as exc:
            raise CliError(f"--job-id must be a UUID, not {args.job_id!r}.") from exc
        found = db.get(Job, job_uuid)
        if found is None:
            raise CliError(f"No job with id {args.job_id}.")
        job = found

    match = db.execute(
        select(JobMatch).where(JobMatch.user_id == user.id, JobMatch.job_id == job.id)
    ).scalar_one_or_none()
    if match is None:
        matching.score_for_user(db, user, notify=False)
        match = db.execute(
            select(JobMatch).where(JobMatch.user_id == user.id, JobMatch.job_id == job.id)
        ).scalar_one_or_none()
    score = match.score if match else 0

    _heading(f"{job.title} at {job.company}")
    print(f"Source          : {job.connector_key} | {job.apply_url or job.source_url}")
    print(f"Score           : {score}")
    if match is not None:
        print(f"Decision        : {match.decision}")
        print(f"Why             : {one_line_reason(match)}")

    # PROHIBITED means we never SUBMIT here. It does not mean we refuse to
    # write anything: a tailored resume for a job you send yourself is the
    # entire point of a review-only platform, and it is what the web app and
    # quick-add already do. Declining to draft would leave the two front doors
    # disagreeing about the same job, and would remove the only help available
    # on the platforms where the user needs it most.
    #
    # What must not happen is a submit, and that is enforced downstream by
    # policy.decide() -- not by skipping the work.
    decision = _preflight(db, user, job, agent_settings, score)
    prohibited = decision.policy == SubmissionPolicy.PROHIBITED.value

    result = workflow.draft_application(db, user, job, match)
    application = result.application

    _heading("Fact guard")
    flags = application.fact_guard_flags or []
    blocking = [flag for flag in flags if flag.get("severity") == "block"]
    if not flags:
        print("Clean: every claim traces to a verified fact.")
    else:
        print(f"{len(flags)} flag(s), {len(blocking)} of them blocking.")
        _table(
            ["SEVERITY", "KIND", "SPAN", "REASON"],
            [
                [
                    flag.get("severity", ""),
                    flag.get("kind", ""),
                    truncate(str(flag.get("span", "")), 30),
                    truncate(str(flag.get("reason", "")), 50),
                ]
                for flag in flags
            ],
        )

    _heading("Critique")
    for name, report in (application.critique or {}).items():
        coverage = round(report.get("keyword_coverage", 0.0) * 100)
        print(
            f"{name}: score {report.get('score', 0)}, keyword coverage {coverage}%, "
            f"{report.get('word_count', 0)} words"
        )
        missing = report.get("missing_keywords") or []
        if missing:
            print(f"  not mentioned: {', '.join(missing[:10])}")
        for finding in report.get("findings", []):
            print(f"  [{finding.get('severity')}] {truncate(finding.get('detail', ''), 96)}")
            if finding.get("suggestion"):
                print(f"      try: {truncate(finding['suggestion'], 88)}")

    _heading("Documents")
    root = _storage_root()
    print(f"Written under   : {root}" if root else "Written to object storage under these keys:")
    _table(
        ["KIND", "LABEL", "TYPE", "BYTES", "FILE"],
        [
            [
                document.kind,
                truncate(document.label, 30),
                document.content_type,
                document.size_bytes,
                _relative_key(document.storage_key) if root else document.storage_key,
            ]
            for document in result.documents
        ],
        aligns="lllrl",
    )

    # A PDF is parsed by an ATS as a drawing that happens to contain text, so
    # every one is read back before it is kept. Reported like the fact guard
    # above: a clean pass is one line, a failure names what a parser would miss.
    _heading("PDF text layer")
    if not result.text_layer_reports:
        print("No PDF was rendered.")
    for role, report in result.text_layer_reports.items():
        label = role.replace("_", " ")
        if report.get("ok"):
            print(
                f"{label}: readable on {report.get('page_count', 0)} page(s), "
                f"{round(report.get('extracted_ratio', 0.0) * 100)}% of the words extracted."
            )
            continue
        print(f"{label}: FAILED -- this PDF was not attached.")
        if report.get("error"):
            print(f"  could not be read back: {report['error']}")
        if report.get("missing_words"):
            print(f"  words lost: {', '.join(report['missing_words'][:10])}")
        if report.get("corrupt_characters"):
            print(f"  characters mangled: {', '.join(report['corrupt_characters'][:10])}")

    _heading("Policy")
    print(f"Status          : {application.status}")
    print(f"Policy          : {decision_label(result.decision)}")
    if prohibited:
        # Say the quiet part out loud. The user asked to apply; they got
        # documents and no submission, and the difference has to be
        # unmissable rather than inferred from a policy string.
        print(
            f"Auto-submit     : never attempted -- {job.connector_key} prohibits it in "
            f"their terms, and no grant can override that."
        )
        print(f"Apply yourself  : {job.apply_url or job.source_url}")
    if result.decision.granted_policy:
        print(f"You authorized  : {result.decision.granted_policy}")
    for line in result.decision.rationale:
        print(f"  {line}")
    if result.decision.review_reasons:
        print(f"Review reasons  : {', '.join(result.decision.review_reasons)}")
    if result.validation_errors:
        print(f"Validation      : {len(result.validation_errors)} error(s)")
        for error in result.validation_errors:
            print(f"  {error}")
    for question in result.blocking_questions:
        print(f"  needs you: {question['question']} ({question['reason']})")
    if result.review_task is not None:
        print(f"Review task     : {result.review_task.id} ({result.review_task.reason})")
    return EXIT_OK


def decision_label(decision: policy.PolicyDecision) -> str:
    if decision.may_submit:
        return f"{decision.policy} -- may be submitted automatically"
    if decision.may_autofill:
        return f"{decision.policy} -- the assistant fills the form, you press submit"
    return f"{decision.policy} -- queued for you to review"


#: Facts are read out grouped, so the user reviews like with like rather than
#: in whatever order the parser happened to emit them.
_FACT_FIELDS: tuple[tuple[str, str], ...] = (
    ("organization", "Organization"),
    ("title", "Title"),
    ("location", "Location"),
    ("evidence_url", "Evidence"),
)


def _fact_dates(fact: CareerFact) -> str:
    if not fact.start_date and not fact.end_date:
        return ""
    start = fact.start_date.isoformat() if fact.start_date else "?"
    end = "present" if fact.is_current else (fact.end_date.isoformat() if fact.end_date else "?")
    return f"{start} -- {end}"


def _print_fact(fact: CareerFact) -> None:
    """One fact, verbatim.

    Nothing here summarises, tidies or truncates the stored strings: the user
    is confirming the exact text that will end up in a document, so showing
    them a cleaned-up version would be confirming something else.
    """
    state = "verified" if fact.verified else "UNVERIFIED"
    print(f"  [{state}] {fact.id}")
    for field, label in _FACT_FIELDS:
        value = getattr(fact, field, None)
        if value:
            print(f"      {label}: {value}")
    dates = _fact_dates(fact)
    if dates:
        print(f"      Dates: {dates}")
    print(f"      Value: {fact.value}")
    for highlight in fact.highlights or []:
        print(f"      - {highlight}")
    if fact.sensitive:
        print("      (marked sensitive: do not echo this into a shared summary)")


def cmd_verify_facts(db: Session, args: argparse.Namespace) -> int:
    """List the parsed career facts, and mark one verified once the user says so.

    Verification is the load-bearing claim in this whole system: the document
    generator drops every unverified fact before it writes a line, the fact
    guard flags anything in the output that does not trace back to one, and
    only verified facts feed the semantic component of ranking.

    So this subcommand exists specifically so that nobody has to reach past it.
    Marking a fact verified goes through POST /profile/facts/verify -- the same
    function the web app calls, with the same audit record -- and it marks
    exactly the one fact named on the command line. There is deliberately no
    --all: a batch "yes" to a list is not a confirmation of each item on it,
    and this is not the place to invent a second, weaker gate.
    """
    user = resolve_user(db, args.email)
    profile = _require_profile(db, user)

    if args.fact_id:
        try:
            fact_uuid = uuid.UUID(args.fact_id)
        except ValueError as exc:
            raise CliError(f"--fact-id must be a UUID, not {args.fact_id!r}.") from exc
        try:
            changed = verify_facts_route(
                VerifyFactsIn(fact_ids=[fact_uuid], verified=not args.unverify), db, user
            )
        except HTTPException as exc:
            raise CliError(f"{exc.detail} (fact {args.fact_id})") from exc
        verb = "unverified" if args.unverify else "verified"
        for fact in changed:
            print(f"Marked {verb}:")
            _print_fact(fact)
        return EXIT_OK

    facts = list_facts(db, user, verified=None if args.all else False, category=args.category)
    if not facts:
        if args.all:
            print(
                f"{user.email} has no career facts yet. Upload a resume on the Profile page "
                "and the parser will propose some."
            )
        else:
            print("Nothing is unverified. Every stored fact has been confirmed.")
        return EXIT_OK

    by_category: dict[str, list[CareerFact]] = {}
    for fact in facts:
        by_category.setdefault(fact.category, []).append(fact)

    unverified = sum(1 for fact in facts if not fact.verified)
    _heading(f"Career facts for {user.email}")
    print(f"Profile         : {profile.full_name or '(no name on file)'}")
    print(f"Shown           : {len(facts)} ({unverified} unverified)")
    for category in sorted(by_category):
        _heading(category.replace("_", " ").title())
        for fact in by_category[category]:
            _print_fact(fact)

    _heading("Next")
    print("Read each fact back to the user exactly as printed and ask for a plain yes or no.")
    print("A fact they confirm, one at a time:")
    print("  python -m app.cli verify-facts --fact-id <id>")
    print("Anything they are unsure about stays unverified. Unverified is the safe state.")
    print("Correct a wrong fact on the Profile page first, then verify the corrected text.")
    return EXIT_OK


def cmd_add_portal(db: Session, args: argparse.Namespace) -> int:
    """Find a company's board on a documented public API, and subscribe to one.

    The probing is services/board_finder's, reached through the same route the
    web app's Settings -> Sources uses, so this command inherits its limits
    rather than restating them: it probes the five documented public job-board
    APIs this project already integrates and nothing else -- no search engines,
    no arbitrary URLs, no HTML.

    Subscribing goes through POST /sources, which is where the compliance
    admission check lives: a MANUAL_ONLY connector is refused there, and a
    connector missing its credentials is refused there. Nothing in this
    function decides whether a source is allowed.
    """
    user = resolve_user(db, args.email)

    if args.catalog:
        entries = source_catalog(db, user)
        _heading("Curated sources")
        _table(
            ["CONNECTOR", "IDENTIFIER", "NAME", "AVAILABLE", "ADDED"],
            [
                [
                    entry.connector_key,
                    truncate(entry.identifier, 30),
                    truncate(entry.display_name, 30),
                    _yes_no(entry.available),
                    _yes_no(entry.already_added),
                ]
                for entry in entries
            ],
        )
        print()
        print("Add one with: python -m app.cli add-portal --add <connector> --identifier <id>")
        return EXIT_OK

    if args.add:
        if not args.identifier:
            raise CliError("--identifier is required with --add (the board token or feed id).")
        try:
            created = create_source(
                SubscriptionIn(
                    connector_key=args.add,
                    identifier=args.identifier,
                    display_name=args.company or "",
                ),
                db,
                user,
            )
        except HTTPException as exc:
            raise CliError(str(exc.detail)) from exc
        print(f"Subscribed to {created.connector_key}/{created.identifier} (source {created.id}).")
        print("It is validated on its first discovery run. If that run comes back")
        print("blocked_by_policy the source disables itself and is not retried.")
        return EXIT_OK

    if not args.company:
        raise CliError(
            "Name a company to search for, or pass --catalog for the curated list. "
            "Only company names are searched: this command probes five documented "
            "public job-board APIs, never an arbitrary URL."
        )

    try:
        found = find_company_boards(BoardSearchIn(company=args.company), db, user)
    except HTTPException as exc:  # pragma: no cover - the route raises none today
        raise CliError(str(exc.detail)) from exc

    _heading(f"Boards found for {args.company}")
    if not found.candidates:
        print("No public board found. That is a normal outcome: many companies do not run one.")
        print("The supported alternative is pasting an individual posting:")
        print(
            "  python -m app.cli apply --url <link> --company ... --title ... "
            "--description-file ..."
        )
        return EXIT_OK

    _table(
        ["CONNECTOR", "IDENTIFIER", "JOBS", "ADDED", "BOARD"],
        [
            [
                candidate.connector_key,
                truncate(candidate.identifier, 24),
                candidate.job_count,
                _yes_no(candidate.already_added),
                truncate(candidate.url, 48),
            ]
            for candidate in found.candidates
        ],
        aligns="llrll",
    )
    print()
    print("Subscribe to one with:")
    print("  python -m app.cli add-portal --add <connector> --identifier <identifier>")
    return EXIT_OK


def cmd_report(db: Session, args: argparse.Namespace) -> int:
    user = resolve_user(db, args.email)
    destination = (
        Path(args.out) if args.out else settings.storage_root / "reports" / "dashboard.html"
    ).resolve()

    # html_report renders and deliberately never writes, so choosing the file is
    # this command's job. One self-contained document: no directory of assets to
    # keep next to it.
    document = html_report.render_report(db, user, hours=max(1, min(720, args.hours)))
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(document, encoding="utf-8")
    except OSError as exc:
        raise CliError(f"Cannot write {destination}: {exc.strerror or exc}") from exc
    print(destination)
    return EXIT_OK


# --------------------------------------------------------------------- wiring
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description=(
            "Drive the job agent from a terminal. Same database, same services and "
            "same policy gate as the web app, so everything done here shows up there."
        ),
    )
    # The engine logs at INFO by default, straight to stdout. That is right for
    # a server and wrong for a report you are reading, so the CLI turns it down
    # and lets an operator turn it back up when a run needs explaining.
    parser.add_argument(
        "--log-level",
        default="WARNING",
        help="engine log level; INFO or DEBUG interleaves service logs with the output",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        sub = subcommands.add_parser(name, help=help_text, description=help_text)
        sub.add_argument(
            "--email", default="", help="account to act as (default: the sole owner account)"
        )
        return sub

    def add_listing(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--limit", type=int, default=10, help="how many matches to print")
        sub.add_argument("--min-score", type=int, default=0, help="hide matches below this score")

    status = add("status", "Show profile, facts, sources, portal readiness and the six buckets")
    status.add_argument("--hours", type=int, default=24, help="window for the bucket counts")
    status.set_defaults(handler=cmd_status)

    scrape = add("scrape", "Poll every enabled source, score what came back, print the top matches")
    add_listing(scrape)
    scrape.set_defaults(handler=cmd_scrape)

    rank = add("rank", "Re-score existing jobs against the current profile, without discovering")
    add_listing(rank)
    rank.add_argument(
        "--scan-limit",
        type=int,
        default=500,
        help="how many stored jobs the scoring pass covers (default 500, max 2000)",
    )
    rank.set_defaults(handler=cmd_rank)

    facts = add(
        "verify-facts", "List parsed career facts, and mark one verified once the user confirms it"
    )
    facts.add_argument(
        "--fact-id", default="", help="mark this one fact verified (omit to list, never to batch)"
    )
    facts.add_argument(
        "--unverify", action="store_true", help="with --fact-id, take the verification back"
    )
    facts.add_argument("--all", action="store_true", help="list verified facts too, not just open")
    facts.add_argument("--category", default=None, help="only this category, e.g. employment")
    facts.set_defaults(handler=cmd_verify_facts)

    portal = add("add-portal", "Find a company's public job board, and subscribe to one")
    portal.add_argument("company", nargs="?", default="", help="company name to search for")
    portal.add_argument("--catalog", action="store_true", help="list the curated sources instead")
    portal.add_argument("--add", default="", help="connector key of the board to subscribe to")
    portal.add_argument("--identifier", default="", help="board token or feed id, with --add")
    portal.set_defaults(handler=cmd_add_portal)

    apply_cmd = add("apply", "Draft an application for one job, then report what the gate decided")
    apply_cmd.add_argument("--job-id", default="", help="id of a job already in the database")
    apply_cmd.add_argument("--url", default="", help="link to a job anywhere; nothing is fetched")
    apply_cmd.add_argument("--company", default="", help="employer name (required with --url)")
    apply_cmd.add_argument("--title", default="", help="job title (required with --url)")
    apply_cmd.add_argument(
        "--description-file",
        default="",
        help="file holding the pasted job description (required with --url)",
    )
    apply_cmd.add_argument("--location", default="", help="location as the posting states it")
    apply_cmd.set_defaults(handler=cmd_apply)

    report = add("report", "Write the HTML dashboard and print where it went")
    report.add_argument("--out", default="", help="destination file for the report")
    report.add_argument("--hours", type=int, default=24, help="window for the bucket counts")
    report.set_defaults(handler=cmd_report)

    return parser


def dispatch(db: Session, args: argparse.Namespace) -> int:
    """Run one subcommand against an open session.

    A refusal is not a crash: work already done -- a pasted job saved and
    scored, a source marked as failed -- stays saved, exactly as it would if
    the same request had been refused through the web app.
    """
    try:
        return args.handler(db, args)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level, settings.log_format)
    try:
        with session_scope() as db:
            return dispatch(db, args)
    except Exception as exc:  # noqa: BLE001 - a traceback is not an error message
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
