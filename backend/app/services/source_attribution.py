"""Work out which platform a pasted job URL belongs to.

Quick-add used to file every pasted job as `manual`, which threw away the one
thing the URL reliably tells us. That mattered in both directions:

  * A Naukri link recorded as `manual` inherits REVIEW_REQUIRED, the generic
    default -- indistinguishable from a job typed in by hand. The job is from a
    platform we may never automate, and nothing in the row said so.
  * A Greenhouse link recorded as `manual` loses the opposite fact: that its
    form IS one the assistant knows how to fill, on a platform the user may
    have authorised.

Attribution is deliberately host-based and exact. A URL is a claim about origin
and nothing more -- we never fetch it to confirm, because for the prohibited
platforms fetching is the exact thing that is off limits.
"""

from __future__ import annotations

from urllib.parse import urlparse

from app.connectors import registry
from app.core.enums import ComplianceTier, SubmissionPolicy

#: host suffix -> connector key. Suffix matching so `boards.greenhouse.io` and
#: `job-boards.greenhouse.io` both land, without a substring match that would
#: let `greenhouse.io.evil.com` through.
_HOST_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("greenhouse.io", "greenhouse"),
    ("lever.co", "lever"),
    ("ashbyhq.com", "ashby"),
    ("workable.com", "workable"),
    ("smartrecruiters.com", "smartrecruiters"),
    ("linkedin.com", "linkedin"),
    ("indeed.com", "indeed"),
    ("naukri.com", "naukri"),
    ("naukrigulf.com", "naukri"),
)


def connector_key_for_url(url: str) -> str:
    """Return the connector key a URL belongs to, or "manual" if unrecognised.

    Unrecognised is the safe answer, not a failure: most jobs live on a company
    careers page nobody has a rule for, and `manual` already means exactly that.
    """
    host = (urlparse(url.strip()).hostname or "").lower().rstrip(".")
    if not host:
        return "manual"
    for suffix, key in _HOST_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return key
    return "manual"


def attribute(url: str) -> tuple[str, str, str]:
    """(connector_key, compliance_tier, submission_policy) for a pasted URL.

    The policy comes from the connector's own declaration rather than a copy
    kept here, so a platform pinned to PROHIBITED in the registry cannot be
    quietly downgraded to REVIEW_REQUIRED by pasting a link to it.
    """
    key = connector_key_for_url(url)
    if key == "manual":
        return "manual", ComplianceTier.MANUAL_ONLY.value, SubmissionPolicy.REVIEW_REQUIRED.value

    connector = registry.get(key)
    return (
        key,
        connector.compliance_tier.value,
        connector.submission_policy_default.value,
    )
