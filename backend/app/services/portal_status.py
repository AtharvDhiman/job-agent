"""Per-portal readiness: what each ATS can do for this user, and what is off.

This is a REPORTING view. It answers "if a matching job arrived right now, how
far would it get on this platform, and which switch is stopping it" so the
dashboard can say so in words instead of leaving the user to guess.

services/policy.py remains the ONLY authority at action time. Nothing here
grants anything: it reads the same inputs the policy gate reads (the connector
registry, the user's PlatformAuthorization, the global kill-switch, the per-user
pause, the daily limit) and describes them. A portal reported as "ready" still
goes through policy.decide() before a single field is filled, and a disagreement
between this module and that one must always be resolved in favour of decide().
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.connectors import registry
from app.connectors.base import BaseConnector
from app.core.config import settings as app_settings
from app.core.enums import AUTOMATION_POLICIES, ComplianceTier, SubmissionPolicy
from app.models.job import JobSourceSubscription
from app.models.user import AgentSettings, PlatformAuthorization, User
from app.services import application_workflow as workflow

#: Statuses in the order the UI should show them: closest to working first.
STATUS_ORDER = ("ready", "authorized", "discovery_only", "blocked", "unsupported")

#: last_status values that mean the most recent poll of a source did not work.
_FAILED_SOURCE_STATUSES = ("error", "blocked_by_policy")


def _short_name(connector: type[BaseConnector]) -> str:
    """Trim the parenthetical: 'LinkedIn (partner API only)' -> 'LinkedIn'.

    The blockers are sentences a human reads, and the full display name reads
    badly mid-sentence.
    """
    return connector.display_name.split(" (")[0].strip() or connector.key


def _source_rollup(db: Session, user: User) -> dict[str, dict[str, Any]]:
    """Per-connector counters over this user's own subscriptions only.

    Scoped to user_id here rather than in the caller so a portal view can never
    accidentally report another user's boards.
    """
    rows = db.execute(
        select(
            JobSourceSubscription.connector_key,
            func.count(JobSourceSubscription.id),
            func.sum(func.coalesce(JobSourceSubscription.jobs_seen, 0)),
            func.max(JobSourceSubscription.last_run_at),
        )
        .where(JobSourceSubscription.user_id == user.id)
        .group_by(JobSourceSubscription.connector_key)
    ).all()

    enabled = dict(
        db.execute(
            select(JobSourceSubscription.connector_key, func.count(JobSourceSubscription.id))
            .where(
                JobSourceSubscription.user_id == user.id,
                JobSourceSubscription.enabled.is_(True),
            )
            .group_by(JobSourceSubscription.connector_key)
        ).all()
    )
    errored = dict(
        db.execute(
            select(JobSourceSubscription.connector_key, func.count(JobSourceSubscription.id))
            .where(
                JobSourceSubscription.user_id == user.id,
                JobSourceSubscription.last_status.in_(_FAILED_SOURCE_STATUSES),
            )
            .group_by(JobSourceSubscription.connector_key)
        ).all()
    )

    rollup: dict[str, dict[str, Any]] = {}
    for key, total, jobs_seen, last_run_at in rows:
        rollup[key] = {
            "source_count": int(total or 0),
            "enabled_source_count": int(enabled.get(key, 0)),
            "jobs_seen": int(jobs_seen or 0),
            "last_run_at": last_run_at,
            "error_count": int(errored.get(key, 0)),
        }
    return rollup


def _active_authorizations(db: Session, user: User) -> dict[str, str]:
    """platform_key -> granted policy, for unrevoked grants only."""
    return {
        row.platform_key: row.policy
        for row in db.execute(
            select(PlatformAuthorization).where(PlatformAuthorization.user_id == user.id)
        ).scalars()
        if row.is_active
    }


def portal_states(db: Session, user: User, agent_settings: AgentSettings) -> list[dict[str, Any]]:
    """One derived PortalState per registered connector. Never stored."""
    rollup = _source_rollup(db, user)
    grants = _active_authorizations(db, user)
    global_enabled = bool(app_settings.automation_global_enabled)
    user_enabled = bool(agent_settings.automation_enabled)
    submitted_today = workflow.applications_today(db, user.id)
    daily_limit = agent_settings.daily_application_limit
    limit_reached = submitted_today >= daily_limit

    states: list[dict[str, Any]] = []
    for connector in registry.all():
        counters = rollup.get(
            connector.key,
            {
                "source_count": 0,
                "enabled_source_count": 0,
                "jobs_seen": 0,
                "last_run_at": None,
                "error_count": 0,
            },
        )
        described = connector.describe(app_settings)
        name = _short_name(connector)

        prohibited = connector.submission_policy_default is SubmissionPolicy.PROHIBITED
        discovery_impossible = connector.compliance_tier is ComplianceTier.MANUAL_ONLY
        granted_policy = grants.get(connector.key)
        # A grant that is merely "review_required" is not permission to submit;
        # policy.decide() treats it the same way.
        has_grant = granted_policy in {policy.value for policy in AUTOMATION_POLICIES}

        blockers: list[str] = []
        if discovery_impossible:
            status = "unsupported"
            blockers.append(
                f"{name} cannot discover jobs automatically; you add each posting yourself."
            )
        elif prohibited:
            status = "blocked"
            blockers.append(f"{name} prohibits automated applying in its terms.")
            blockers.append(
                f"Matched {name} roles always become review tasks with a link you open yourself."
            )
        elif not connector.browser_submission_supported:
            status = "discovery_only"
            blockers.append(
                f"{name} has no supported browser application workflow, so applications "
                "are drafted for you to submit yourself."
            )
        else:
            if not has_grant:
                status = "discovery_only"
                blockers.append(f"Not authorized for auto-submit on {name}")
            else:
                status = "ready"
            if not global_enabled:
                blockers.append("Automation is switched off on the server")
            if not user_enabled:
                reason = agent_settings.paused_reason
                blockers.append("Automation is paused" + (f" ({reason})" if reason else ""))
            if limit_reached:
                blockers.append(f"Daily limit of {daily_limit} reached")
            # Anything above means a submit would not happen right now, so an
            # authorized portal is reported as authorized rather than ready.
            if status == "ready" and blockers:
                status = "authorized"

        if described["required_credentials"] and not described["available"]:
            blockers.append("Missing credentials: " + ", ".join(described["required_credentials"]))
        if counters["source_count"] == 0:
            blockers.append("No source configured")
        elif counters["enabled_source_count"] == 0:
            blockers.append("Every configured source is disabled")
        if counters["error_count"]:
            blockers.append(f"{counters['error_count']} source(s) failed on the last run")

        states.append(
            {
                "key": connector.key,
                "display_name": connector.display_name,
                "status": status,
                "compliance_tier": described["compliance_tier"],
                "browser_submission_supported": described["browser_submission_supported"],
                "automation_permitted_for_submission": described[
                    "automation_permitted_for_submission"
                ],
                "granted_policy": granted_policy,
                "credentials_required": described["required_credentials"],
                # `available` is exactly "every required env var is non-empty".
                "credentials_present": described["available"],
                "source_count": counters["source_count"],
                "enabled_source_count": counters["enabled_source_count"],
                "last_run_at": counters["last_run_at"],
                "jobs_seen": counters["jobs_seen"],
                "error_count": counters["error_count"],
                "blockers": blockers,
            }
        )

    states.sort(key=lambda state: (STATUS_ORDER.index(state["status"]), state["key"]))
    return states
