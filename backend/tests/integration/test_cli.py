"""The terminal front door drives the same engine, and the same gate, as the API.

These tests call the subcommands against the test session directly rather than
shelling out, so what they assert is that `apply` reaches services/policy.py and
obeys it -- not that a subprocess happened to exit 0.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest
from sqlalchemy import select

from app.cli import build_parser, dispatch
from app.models.application import Application
from app.models.job import Job
from tests.conftest import make_job

pytestmark = pytest.mark.integration

#: The slash-command files that tell an agent how to drive this CLI. They are
#: the only instructions it gets, so a flag renamed here and not there is a
#: command that fails in front of a user.
COMMANDS_DIR = Path(__file__).resolve().parents[3] / ".claude" / "commands"


def run(db, *argv: str) -> int:
    """Parse a real command line, then run it against the test session."""
    return dispatch(db, build_parser().parse_args(list(argv)))


def _documented_invocations() -> list[tuple[str, list[str]]]:
    """Every `-m app.cli ...` line in the command files, as argv.

    Placeholders (`$ARGUMENTS`, `<job-id>`) become a plain string: argparse
    validates flag NAMES and types, which is what these files keep getting
    wrong, and no value in them is typed as anything but a string.
    """
    found: list[tuple[str, list[str]]] = []
    for path in sorted(COMMANDS_DIR.glob("*.md")):
        # Shell line continuations first, so a multi-line example is one command.
        text = path.read_text(encoding="utf-8").replace("\\\n", " ")
        for line in text.splitlines():
            _, marker, rest = line.partition("-m app.cli")
            if not marker or not rest.strip():
                continue
            # Prose mentions wrap the command in backticks; cut at the closer.
            rest = rest.split("`")[0]
            argv = [
                "placeholder" if token.startswith(("$", "<")) or token.endswith(">") else token
                for token in shlex.split(rest)
            ]
            found.append((f"{path.name}: {rest.strip()}", argv))
    return found


# --------------------------------------------------- the command files agree
@pytest.mark.skipif(not COMMANDS_DIR.is_dir(), reason="command files are not in this checkout")
def test_every_documented_invocation_actually_parses():
    """The .claude/commands files are executable instructions, so they are tested.

    Every one of these had drifted from argparse at once: subcommands that were
    never built, a positional where the parser takes a flag, `--days` where the
    parser takes `--hours`. Each failed in front of a user with an argparse
    error and an exit code of 2. Nothing caught it, because prose is not code --
    so this makes it code.
    """
    parser = build_parser()
    invocations = _documented_invocations()
    assert invocations, "no invocations found; has the extraction stopped matching?"

    failures = []
    for label, argv in invocations:
        try:
            parser.parse_args(argv)
        except SystemExit:
            failures.append(label)
    assert not failures, "these documented commands do not parse:\n  " + "\n  ".join(failures)


@pytest.mark.skipif(not COMMANDS_DIR.is_dir(), reason="command files are not in this checkout")
def test_every_subcommand_has_a_command_file_and_vice_versa():
    """A subcommand with no file is undiscoverable; a file with no subcommand is a dead end."""
    subcommands = set(build_parser()._subparsers._group_actions[0].choices)  # noqa: SLF001
    documented = {path.stem for path in COMMANDS_DIR.glob("*.md")}

    assert documented == subcommands


@pytest.mark.skipif(not COMMANDS_DIR.is_dir(), reason="command files are not in this checkout")
def test_no_command_file_documents_a_flag_the_parser_does_not_have():
    """Catches the quieter half: a flag that is accepted but means something else.

    `--days` errored loudly. A flag that exists but does nothing the doc claims
    would not, so every long flag named in a fenced command block is checked
    against the subparser it is used with.
    """
    parser = build_parser()
    choices = parser._subparsers._group_actions[0].choices  # noqa: SLF001

    unknown = []
    for label, argv in _documented_invocations():
        if not argv or argv[0] not in choices:
            continue
        known = {option for action in choices[argv[0]]._actions for option in action.option_strings}
        unknown += [
            f"{label} -> {token}"
            for token in argv[1:]
            if token.startswith("--") and token not in known
        ]
    assert not unknown, "flags documented but not defined:\n  " + "\n  ".join(unknown)


# ------------------------------------------------------------------ discovery
def test_status_runs_on_an_empty_database(db, user, capsys):
    """A brand new account has no profile, facts, sources or jobs. It must still
    print a report, because "what is missing" is exactly what status is for."""
    assert run(db, "status") == 0

    out = capsys.readouterr().out
    assert user.email in out
    assert "No candidate profile yet" in out
    assert "No sources configured" in out
    # The six buckets are the point of the command; all six must be named.
    for label in ("New jobs found", "High match", "Queued for auto-submit"):
        assert label in out
    for label in ("Needs review", "Submitted", "Failed or stopped"):
        assert label in out
    assert "Next steps" in out


def test_status_reports_profile_completeness_and_facts(db, user, profile, facts, capsys):
    assert run(db, "status") == 0

    out = capsys.readouterr().out
    assert "Completeness" in out
    # Four of the five fixture facts are verified; the fifth deliberately is not.
    assert "Verified facts  : 4 of 5" in out
    # Portal readiness comes from services/portal_status, not from this module.
    assert "Portals" in out


def test_unknown_email_exits_non_zero(db, user, capsys):
    assert run(db, "status", "--email", "nobody@example.com") != 0

    captured = capsys.readouterr()
    assert "nobody@example.com" in captured.err
    # A message, never a traceback.
    assert "Traceback" not in captured.err


def test_no_account_at_all_exits_non_zero(db, capsys):
    assert run(db, "status") != 0
    assert "No owner account" in capsys.readouterr().err


def test_status_writes_nothing_not_even_the_settings_row(db, user, capsys):
    """status promises to change nothing, so it must not create the row it prints.

    deps.get_agent_settings INSERTs the missing AgentSettings row, and
    session_scope commits it -- which made printing the automation
    configuration the act that created it.
    """
    from app.models.user import AgentSettings

    db.query(AgentSettings).delete()
    db.flush()

    assert run(db, "status") == 0

    assert "Auto-submit at" in capsys.readouterr().out
    assert db.query(AgentSettings).count() == 0


def test_email_does_not_bypass_the_role_check(db, user, capsys):
    """--email is not a way past RequireOperator.

    Every subcommand hands the resolved user to route functions the web app
    guards with RequireOperator, so a viewer reaching them from a terminal
    would be doing what the API answers with 403.
    """
    from app.core.security import Role, hash_password
    from app.models.user import User

    viewer = User(
        email="viewer@example.com",
        hashed_password=hash_password("Sup3r-Secret-Passw0rd!"),
        full_name="Read Only",
        role=Role.VIEWER.value,
    )
    db.add(viewer)
    db.flush()

    assert run(db, "rank", "--email", "viewer@example.com") != 0

    err = capsys.readouterr().err
    assert "viewer" in err
    assert "operator" in err


def test_email_does_not_bypass_the_active_check(db, user, capsys):
    from app.core.security import Role, hash_password
    from app.models.user import User

    retired = User(
        email="retired@example.com",
        hashed_password=hash_password("Sup3r-Secret-Passw0rd!"),
        full_name="Retired Owner",
        role=Role.OWNER.value,
        is_active=False,
    )
    db.add(retired)
    db.flush()

    assert run(db, "rank", "--email", "retired@example.com") != 0
    assert "deactivated" in capsys.readouterr().err


def test_zero_years_of_experience_counts_as_answered(db, user, profile, capsys):
    """years_experience is Numeric(4, 1), so it comes back as a falsy Decimal.

    A graduate who answered "0" was told to go and fill in the field they had
    just filled in.
    """
    profile.years_experience = 0
    db.flush()
    db.expire(profile)

    assert run(db, "status") == 0
    out = capsys.readouterr().out
    still_needed = next((line for line in out.splitlines() if "Still needed" in line), "")
    assert "years of experience" not in still_needed


# -------------------------------------------------------------------- ranking
def test_rank_scores_and_prints_the_top_matches(db, user, profile, facts, job, capsys):
    assert run(db, "rank", "--limit", "5") == 0

    out = capsys.readouterr().out
    assert "Scored 1" in out
    assert job.title in out
    assert job.company in out
    # The one-line reason is built from what the ranker stored, not invented.
    assert "python" in out


def test_scrape_without_sources_still_scores(db, user, profile, facts, job, capsys):
    assert run(db, "scrape") == 0

    out = capsys.readouterr().out
    assert "No enabled sources" in out
    assert "Scoring" in out


def test_min_score_hides_weaker_matches(db, user, profile, facts, job, capsys):
    """A filter that hid everything must say so, not claim nothing was scored.

    "Nothing scored yet. Run `scrape`..." contradicted the count printed two
    lines above it and sent the user to a command that cannot help: the jobs
    exist, and --min-score is what removed them from the table.
    """
    assert run(db, "rank", "--min-score", "101") == 0

    out = capsys.readouterr().out
    assert "Scored 1" in out
    assert "No match scored 101 or higher" in out
    assert "Nothing scored yet" not in out
    assert "scrape" not in out


def test_a_genuinely_empty_database_still_says_to_scrape(db, user, profile, facts, capsys):
    """The other half of the branch above: no matches and no filter."""
    assert run(db, "rank") == 0
    assert "Nothing scored yet" in capsys.readouterr().out


def test_rank_scan_limit_reaches_the_scorer(db, user, profile, facts, job, capsys):
    """--scan-limit sizes the scoring pass; --limit only sizes the printed table."""
    assert run(db, "rank", "--scan-limit", "1", "--limit", "5") == 0
    assert "Scored 1" in capsys.readouterr().out


# ---------------------------------------------------------------------- apply
def test_apply_drafts_documents_and_critiques_them(db, user, profile, facts, job, capsys):
    assert run(db, "apply", "--job-id", str(job.id)) == 0

    out = capsys.readouterr().out
    assert "Fact guard" in out
    assert "keyword coverage" in out
    assert "resume_generated" in out
    assert "queued for you to review" in out

    application = db.execute(
        select(Application).where(Application.user_id == user.id, Application.job_id == job.id)
    ).scalar_one()
    assert application.status == "needs_review"
    assert "resume" in application.critique
    assert application.critique["resume"]["score"] >= 0


def test_apply_drafts_for_a_prohibited_platform_but_never_submits(db, user, profile, facts, capsys):
    """PROHIBITED means we never submit. It does not mean we refuse to help.

    A tailored resume for a job the human sends themselves is the whole point
    of a review-only platform -- and it is what the web app and quick-add
    already do, so a CLI that declined to draft would leave the two front doors
    disagreeing about the same job.
    """
    prohibited = make_job(
        connector_key="linkedin",
        compliance_tier="manual_only",
        submission_policy_default="prohibited",
        source_url="https://www.linkedin.com/jobs/view/1",
        apply_url="https://www.linkedin.com/jobs/view/1",
    )
    db.add(prohibited)
    db.flush()

    assert run(db, "apply", "--job-id", str(prohibited.id)) == 0

    out = capsys.readouterr().out
    assert "never attempted" in out
    assert "https://www.linkedin.com/jobs/view/1" in out

    application = db.execute(
        select(Application).where(Application.job_id == prohibited.id)
    ).scalar_one()
    # Drafted, and parked for the human rather than queued for the assistant.
    assert application.status == "needs_review"
    assert application.submission_policy == "prohibited"


def test_apply_from_a_url_attributes_the_platform_then_parks_it(
    db, user, profile, facts, tmp_path, capsys
):
    """A pasted link goes through quick-add, so the host still decides the policy."""
    description = tmp_path / "job.txt"
    description.write_text(
        "Senior Backend Engineer working in Python, PostgreSQL and AWS. 5+ years.",
        encoding="utf-8",
    )

    exit_code = run(
        db,
        "apply",
        "--url",
        "https://www.linkedin.com/jobs/view/998877",
        "--company",
        "Meridian Cloud",
        "--title",
        "Senior Backend Engineer",
        "--description-file",
        str(description),
    )

    assert exit_code == 0
    assert "never attempted" in capsys.readouterr().out
    # The job itself is kept and attributed, exactly as quick-add leaves it.
    saved = db.execute(select(Job).where(Job.company == "Meridian Cloud")).scalar_one()
    assert saved.connector_key == "linkedin"
    # Drafted for the human; the policy gate is what stops the submit.
    application = db.execute(select(Application).where(Application.job_id == saved.id)).scalar_one()
    assert application.status == "needs_review"


def test_apply_needs_exactly_one_of_job_id_and_url(db, user, profile, capsys):
    assert run(db, "apply") != 0
    assert "exactly one" in capsys.readouterr().err


def test_apply_from_a_url_requires_the_pasted_text(db, user, profile, capsys):
    assert run(db, "apply", "--url", "https://example.com/jobs/1", "--company", "Acme") != 0
    assert "--title is required" in capsys.readouterr().err


def test_apply_without_a_profile_explains_itself(db, user, job, capsys):
    assert run(db, "apply", "--job-id", str(job.id)) != 0
    assert "no candidate profile" in capsys.readouterr().err


def test_apply_rejects_a_job_id_that_is_not_a_uuid(db, user, profile, capsys):
    assert run(db, "apply", "--job-id", "not-a-uuid") != 0
    assert "must be a UUID" in capsys.readouterr().err


# -------------------------------------------------------------- verify-facts
def test_verify_facts_lists_what_is_still_unverified(db, user, profile, facts, capsys):
    assert run(db, "verify-facts") == 0

    out = capsys.readouterr().out
    # The one deliberately-unverified fixture fact, shown verbatim.
    assert "AWS Certified Solutions Architect" in out
    assert "UNVERIFIED" in out
    # ...and the four verified ones are not in the way.
    assert "Northwind Systems" not in out


def test_verify_facts_marks_exactly_the_one_fact_named(db, user, profile, facts, capsys):
    """The human gate, driven through the same route the web app calls."""
    unverified = next(fact for fact in facts if not fact.verified)

    assert run(db, "verify-facts", "--fact-id", str(unverified.id)) == 0

    db.refresh(unverified)
    assert unverified.verified is True
    assert unverified.verified_at is not None
    assert "Marked verified" in capsys.readouterr().out


def test_verify_facts_can_take_a_verification_back(db, user, profile, facts):
    verified = next(fact for fact in facts if fact.verified)

    assert run(db, "verify-facts", "--fact-id", str(verified.id), "--unverify") == 0

    db.refresh(verified)
    assert verified.verified is False
    assert verified.verified_at is None


def test_verify_facts_offers_no_way_to_confirm_a_whole_list(db, user, profile, facts, capsys):
    """There is deliberately no --all-style batch verify.

    A batch "yes" to a long list is not a confirmation of each item on it, and
    an agent that hits an argparse error is more likely to go and ask the user
    than one handed a shortcut.
    """
    import pytest as _pytest

    with _pytest.raises(SystemExit):
        build_parser().parse_args(["verify-facts", "--verify-all"])

    # --all only widens the LISTING; it writes nothing.
    assert run(db, "verify-facts", "--all") == 0
    assert sum(1 for fact in facts if fact.verified) == 4


def test_verify_facts_rejects_an_id_that_is_not_a_uuid(db, user, profile, facts, capsys):
    assert run(db, "verify-facts", "--fact-id", "nope") != 0
    assert "must be a UUID" in capsys.readouterr().err


def test_verify_facts_reports_an_id_that_is_not_this_users(db, user, profile, facts, capsys):
    import uuid as _uuid

    assert run(db, "verify-facts", "--fact-id", str(_uuid.uuid4())) != 0
    err = capsys.readouterr().err
    assert "No matching facts" in err
    assert "Traceback" not in err


# ---------------------------------------------------------------- add-portal
def test_add_portal_lists_the_curated_catalog_without_touching_the_network(db, user, capsys):
    assert run(db, "add-portal", "--catalog") == 0

    out = capsys.readouterr().out
    assert "Curated sources" in out
    assert "CONNECTOR" in out


def test_add_portal_subscribes_through_the_compliance_checked_route(db, user, capsys):
    from app.models.job import JobSourceSubscription

    assert run(db, "add-portal", "--add", "greenhouse", "--identifier", "northwind") == 0

    subscription = db.execute(
        select(JobSourceSubscription).where(JobSourceSubscription.user_id == user.id)
    ).scalar_one()
    assert subscription.connector_key == "greenhouse"
    assert subscription.identifier == "northwind"
    assert "Subscribed to greenhouse/northwind" in capsys.readouterr().out


def test_add_portal_refuses_a_manual_only_platform(db, user, capsys):
    """POST /sources is where that admission check lives, and it still fires.

    Naukri publishes no candidate API and serves 403 for its own robots.txt, so
    it exists in the registry only to explain itself. Reaching it from a
    terminal must not be a way around that.
    """
    assert run(db, "add-portal", "--add", "naukri", "--identifier", "anything") != 0

    err = capsys.readouterr().err
    assert "does not support automated discovery" in err
    assert "Traceback" not in err


def test_add_portal_needs_something_to_do(db, user, capsys):
    assert run(db, "add-portal") != 0
    assert "Name a company" in capsys.readouterr().err


def test_add_portal_add_without_an_identifier_explains_itself(db, user, capsys):
    assert run(db, "add-portal", "--add", "greenhouse") != 0
    assert "--identifier is required" in capsys.readouterr().err


# --------------------------------------------------------------------- report
def test_report_writes_the_html_dashboard(db, user, profile, facts, job, tmp_path, capsys):
    destination = tmp_path / "dashboard.html"
    assert run(db, "report", "--out", str(destination)) == 0

    assert destination.exists()
    assert "<" in destination.read_text(encoding="utf-8")
    assert str(destination) in capsys.readouterr().out
