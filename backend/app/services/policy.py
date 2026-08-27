"""The single decision point for "may we automate this application?".

Nothing else in the codebase is allowed to conclude that automation is
permitted. The API, the workers and the browser assistant all call decide()
and obey the result. Defaults are restrictive: absent evidence of permission,
the answer is REVIEW_REQUIRED.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.connectors import registry
from app.core.enums import ReviewReason, SubmissionPolicy
from app.models.job import Job
from app.models.user import AgentSettings, PlatformAuthorization

#: Platforms whose terms forbid automated applying. Not configurable at runtime.
HARD_PROHIBITED_PLATFORMS = frozenset({"linkedin", "indeed", "naukri"})

#: Review reasons a human pressing "approve" is allowed to lift when the browser
#: assistant later asks for work. They all mean "this application did not clear a
#: bar YOU set" -- a low score, your daily cap, a pre-flight nit you have now
#: read. Every other reason means automation itself is off limits here (the
#: platform is prohibited or was never granted, the kill-switch is off, the
#: content is not traceable to a verified fact) and no approval can lift it.
#: This is an allow-list on purpose: a new ReviewReason is un-liftable until
#: somebody deliberately adds it here.
HUMAN_APPROVAL_MAY_LIFT = frozenset(
    {
        ReviewReason.BELOW_AUTO_SUBMIT_THRESHOLD.value,
        ReviewReason.DAILY_LIMIT_REACHED.value,
        ReviewReason.VALIDATION_FAILED.value,
        ReviewReason.MANUAL_REQUEST.value,
    }
)

#: Lower is stricter. An unrecognised value is treated as PROHIBITED: there is no
#: implicit allow anywhere in this file.
_STRICTNESS = {
    SubmissionPolicy.PROHIBITED.value: 0,
    SubmissionPolicy.REVIEW_REQUIRED.value: 1,
    SubmissionPolicy.ASSISTED_AUTOFILL.value: 2,
    SubmissionPolicy.AUTO_SUBMIT.value: 3,
}


def normalize_platform_key(key: str) -> str:
    """Platform keys are compared case- and whitespace-insensitively.

    'LinkedIn ' must never slip past a frozenset built from lower-case keys.
    """
    return (key or "").strip().lower()


def is_hard_prohibited(platform_key: str) -> bool:
    return normalize_platform_key(platform_key) in HARD_PROHIBITED_PLATFORMS


def registered_connector_policy(connector_key: str) -> str:
    """What the connector registry says about this platform *today*.

    A job row stores the submission policy that was in force when it was
    ingested. If a connector has since been pinned to PROHIBITED, the stale row
    must not win. A key the registry does not know cannot be verified at all, so
    it is treated as prohibited.
    """
    try:
        connector = registry.get(normalize_platform_key(connector_key))
    except Exception:
        return SubmissionPolicy.PROHIBITED.value
    return connector.submission_policy_default.value


def registered_connector_supports_browser_submission(connector_key: str) -> bool:
    """Whether this connector has an explicitly supported browser workflow.

    A public jobs feed is enough for discovery, not enough to let an automated
    browser touch an application page.  New connectors therefore fail closed
    until they opt in here through their contract declaration.
    """
    try:
        connector = registry.get(normalize_platform_key(connector_key))
    except Exception:
        return False
    return bool(connector.browser_submission_supported)


def _strictest(*policies: str) -> str:
    return min(policies, key=lambda value: _STRICTNESS.get(value, 0))


@dataclass(slots=True)
class PolicyDecision:
    policy: str
    #: The authorization the user actually granted for this platform, if any.
    #: Distinguishes "you asked for assisted autofill" from "you asked for
    #: auto-submit but this application did not qualify".
    granted_policy: str = ""
    may_autofill: bool = False
    may_submit: bool = False
    review_reasons: list[str] = field(default_factory=list)
    rationale: list[str] = field(default_factory=list)

    @property
    def requires_review(self) -> bool:
        return not self.may_submit

    def as_dict(self) -> dict:
        return {
            "policy": self.policy,
            "granted_policy": self.granted_policy,
            "may_autofill": self.may_autofill,
            "may_submit": self.may_submit,
            "review_reasons": self.review_reasons,
            "rationale": self.rationale,
        }


def decide(
    *,
    job: Job,
    connector_policy: str,
    authorization: PlatformAuthorization | None,
    agent_settings: AgentSettings,
    score: int,
    global_enabled: bool,
    applications_today: int,
    fact_guard_blocked: bool = False,
    blocking_questions: int = 0,
    validation_errors: int = 0,
) -> PolicyDecision:
    """Return what we are permitted to do with this application, and why."""
    reasons: list[str] = []
    rationale: list[str] = []

    # 1. Platform-level prohibition. Nothing can override this.
    #    The caller passes the policy stored on the job row, which is a snapshot
    #    taken at ingest time; the registry is asked again here so a connector
    #    that has since become PROHIBITED, or one that no longer exists, wins.
    effective_connector_policy = _strictest(
        connector_policy, registered_connector_policy(job.connector_key)
    )
    if (
        is_hard_prohibited(job.connector_key)
        or effective_connector_policy == SubmissionPolicy.PROHIBITED.value
    ):
        return PolicyDecision(
            policy=SubmissionPolicy.PROHIBITED.value,
            review_reasons=[ReviewReason.PLATFORM_PROHIBITS_AUTOMATION.value],
            rationale=[
                f"{job.connector_key} prohibits automated applications. A review task with a "
                "direct link is created instead; you apply yourself."
            ],
        )

    if not registered_connector_supports_browser_submission(job.connector_key):
        return PolicyDecision(
            policy=SubmissionPolicy.REVIEW_REQUIRED.value,
            review_reasons=[ReviewReason.UNSUPPORTED_PLATFORM.value],
            rationale=[
                f"{job.connector_key} can be used to discover jobs, but it does not have a "
                "supported browser application workflow. A review task with a direct link is "
                "created instead."
            ],
        )

    # 2. Explicit, unrevoked authorization for this platform.
    granted = authorization.policy if (authorization and authorization.is_active) else None
    if granted is None:
        reasons.append(ReviewReason.PLATFORM_NOT_AUTHORIZED.value)
        rationale.append(
            f"You have not authorized automation for {job.connector_key}. "
            "Grant it in Settings if you want assisted autofill or auto-submit."
        )
    elif granted == SubmissionPolicy.REVIEW_REQUIRED.value:
        reasons.append(ReviewReason.PLATFORM_NOT_AUTHORIZED.value)
        rationale.append(f"{job.connector_key} is authorized for review-only.")

    # 3. Global kill-switch and per-user pause.
    if not global_enabled or not agent_settings.automation_enabled:
        reasons.append(ReviewReason.AUTOMATION_DISABLED.value)
        rationale.append(
            "Automation is paused"
            + (f" ({agent_settings.paused_reason})" if agent_settings.paused_reason else "")
            + "."
        )

    # 4. Daily limit.
    if applications_today >= agent_settings.daily_application_limit:
        reasons.append(ReviewReason.DAILY_LIMIT_REACHED.value)
        rationale.append(
            f"Daily limit of {agent_settings.daily_application_limit} submissions reached."
        )

    # 5. Confidence threshold.
    if score < agent_settings.auto_submit_min_score:
        reasons.append(ReviewReason.BELOW_AUTO_SUBMIT_THRESHOLD.value)
        rationale.append(
            f"Score {score} is below your auto-submit threshold of "
            f"{agent_settings.auto_submit_min_score}."
        )

    # 6. Content integrity.
    if fact_guard_blocked:
        reasons.append(ReviewReason.FACT_GUARD_FLAGGED.value)
        rationale.append("Generated content contains claims not traceable to a verified fact.")
    if blocking_questions:
        reasons.append(ReviewReason.UNANSWERABLE_QUESTION.value)
        rationale.append(
            f"{blocking_questions} required question(s) could not be answered from verified facts."
        )
    if validation_errors:
        reasons.append(ReviewReason.VALIDATION_FAILED.value)
        rationale.append(f"{validation_errors} pre-flight validation error(s).")

    # --- resolve ---------------------------------------------------------
    if reasons:
        may_autofill = (
            granted
            in (SubmissionPolicy.ASSISTED_AUTOFILL.value, SubmissionPolicy.AUTO_SUBMIT.value)
            and global_enabled
            and agent_settings.automation_enabled
            and not fact_guard_blocked
            and not blocking_questions
        )
        return PolicyDecision(
            policy=SubmissionPolicy.ASSISTED_AUTOFILL.value
            if may_autofill
            else SubmissionPolicy.REVIEW_REQUIRED.value,
            granted_policy=granted or "",
            may_autofill=may_autofill,
            may_submit=False,
            review_reasons=sorted(set(reasons)),
            rationale=rationale,
        )

    if granted == SubmissionPolicy.ASSISTED_AUTOFILL.value:
        return PolicyDecision(
            policy=SubmissionPolicy.ASSISTED_AUTOFILL.value,
            granted_policy=granted,
            may_autofill=True,
            may_submit=False,
            review_reasons=[ReviewReason.MANUAL_REQUEST.value],
            rationale=[
                "Authorized for assisted autofill: the local browser assistant fills the form "
                "and stops at the submit button for you."
            ],
        )

    return PolicyDecision(
        policy=SubmissionPolicy.AUTO_SUBMIT.value,
        granted_policy=granted,
        may_autofill=True,
        may_submit=True,
        rationale=[
            f"Score {score} meets the threshold of {agent_settings.auto_submit_min_score}, "
            f"{job.connector_key} is explicitly authorized for auto-submit, automation is on, "
            "the daily limit is not reached, and all content traces to verified facts."
        ],
    )


def may_hand_out(decision: PolicyDecision, *, approved_by_human: bool) -> bool:
    """May the browser assistant be handed this application at all?

    The single answer to that question, so no route has to assemble one from
    parts. Opening a page and typing into it is automation whether or not the
    submit button is ever clicked, so a PROHIBITED platform is refused here even
    when a human approved the review task, and an approval clears only the
    reasons in HUMAN_APPROVAL_MAY_LIFT.
    """
    if decision.policy == SubmissionPolicy.PROHIBITED.value:
        return False
    if not (decision.may_autofill or approved_by_human):
        return False
    return not (set(decision.review_reasons) - HUMAN_APPROVAL_MAY_LIFT)
