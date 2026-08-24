"""Connector contract.

Every connector must declare a ComplianceTier and a default SubmissionPolicy.
The registry refuses anything that does not -- there is no implicit "allow".
Read docs/COMPLIANCE.md before adding one.
"""

from __future__ import annotations

import abc
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

from app.core.enums import ComplianceTier, SubmissionPolicy


class ConnectorError(RuntimeError):
    """Recoverable connector failure; the run is recorded and retried later."""


class BlockedByPolicyError(ConnectorError):
    """The connector refused to proceed for compliance reasons. Never retried."""


@dataclass(slots=True)
class RawJob:
    """What a connector returns before normalisation."""

    external_id: str
    title: str
    company: str
    source_url: str
    apply_url: str = ""
    description_html: str = ""
    description_text: str = ""
    location_raw: str = ""
    department: str = ""
    employment_type: str = ""
    posted_at: datetime | None = None
    deadline_at: datetime | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str = ""
    salary_period: str = ""
    remote_flag: bool | None = None
    is_direct_employer: bool = True
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SourceSpec:
    """One board to poll: connector key plus its identifier (board token, etc.)."""

    connector_key: str
    identifier: str
    display_name: str = ""
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FetchResult:
    jobs: list[RawJob]
    etag: str = ""
    notes: list[str] = field(default_factory=list)


class BaseConnector(abc.ABC):
    """Subclass, set the class vars, implement fetch()."""

    key: ClassVar[str]
    display_name: ClassVar[str]
    compliance_tier: ClassVar[ComplianceTier]
    submission_policy_default: ClassVar[SubmissionPolicy]
    #: Why this tier was chosen. Surfaced verbatim in the UI and API.
    policy_note: ClassVar[str] = ""
    #: Env var names that must be non-empty before this connector may run.
    required_credentials: ClassVar[tuple[str, ...]] = ()
    #: True when the postings come straight from the employer's own ATS.
    direct_employer: ClassVar[bool] = True
    #: Identifier the user supplies, e.g. "Greenhouse board token".
    identifier_label: ClassVar[str] = "identifier"
    identifier_help: ClassVar[str] = ""

    def __init__(self, http, *, settings=None):
        self.http = http
        self.settings = settings

    @abc.abstractmethod
    def fetch(self, spec: SourceSpec, *, etag: str = "") -> FetchResult:
        """Return postings for one source. Raise ConnectorError on failure."""

    # -- helpers ---------------------------------------------------------
    @classmethod
    def is_available(cls, settings) -> tuple[bool, str]:
        """A connector needing credentials stays invisible until they exist."""
        for name in cls.required_credentials:
            if not getattr(settings, name.lower(), ""):
                return False, f"Requires {name} in the environment (your own API agreement)."
        return True, ""

    @classmethod
    def permits_automated_discovery(cls) -> bool:
        return cls.compliance_tier != ComplianceTier.MANUAL_ONLY

    @classmethod
    def describe(cls, settings=None) -> dict[str, Any]:
        available, reason = (True, "") if settings is None else cls.is_available(settings)
        return {
            "key": cls.key,
            "display_name": cls.display_name,
            "compliance_tier": cls.compliance_tier.value,
            "submission_policy_default": cls.submission_policy_default.value,
            "automation_permitted_for_discovery": cls.permits_automated_discovery(),
            "automation_permitted_for_submission": cls.submission_policy_default
            != SubmissionPolicy.PROHIBITED,
            "requires_user_review_by_default": cls.submission_policy_default
            in (SubmissionPolicy.REVIEW_REQUIRED, SubmissionPolicy.PROHIBITED),
            "policy_note": cls.policy_note,
            "required_credentials": list(cls.required_credentials),
            "available": available,
            "unavailable_reason": reason,
            "direct_employer": cls.direct_employer,
            "identifier_label": cls.identifier_label,
            "identifier_help": cls.identifier_help,
        }


#: Platform keys whose terms forbid automated applying. Duplicated from
#: services/policy.HARD_PROHIBITED_PLATFORMS on purpose: the connector layer must
#: not import the service layer, and a registry that could hand out a
#: non-PROHIBITED LinkedIn connector would make the policy list decorative. The
#: test suite asserts the two lists stay identical.
HARD_PROHIBITED_KEYS = frozenset({"linkedin", "indeed"})


class ConnectorRegistry:
    """Central register. Enforces that every connector declares its policy."""

    def __init__(self) -> None:
        self._connectors: dict[str, type[BaseConnector]] = {}

    def register(self, cls: type[BaseConnector]) -> type[BaseConnector]:
        for attr in ("key", "display_name", "compliance_tier", "submission_policy_default"):
            if not getattr(cls, attr, None):
                raise TypeError(
                    f"{cls.__name__} must declare '{attr}'. Connectors without an explicit "
                    "compliance declaration are not allowed (docs/COMPLIANCE.md)."
                )
        if not isinstance(cls.compliance_tier, ComplianceTier):
            raise TypeError(f"{cls.__name__}.compliance_tier must be a ComplianceTier")
        if not isinstance(cls.submission_policy_default, SubmissionPolicy):
            raise TypeError(f"{cls.__name__}.submission_policy_default must be a SubmissionPolicy")
        key = self._normalize(cls.key)
        if not key:
            raise TypeError(f"{cls.__name__}.key must be a non-empty string")
        if (
            key in HARD_PROHIBITED_KEYS
            and cls.submission_policy_default is not SubmissionPolicy.PROHIBITED
        ):
            raise TypeError(
                f"{cls.__name__} registers as '{key}', whose terms forbid automated "
                "applying. It must declare submission_policy_default = PROHIBITED "
                "(docs/COMPLIANCE.md section 2)."
            )
        if key in self._connectors:
            raise ValueError(f"Duplicate connector key: {cls.key}")
        self._connectors[key] = cls
        return cls

    @staticmethod
    def _normalize(key: str) -> str:
        """Keys are matched case- and whitespace-insensitively.

        Registering 'LinkedIn' must not be a way to get a key the policy layer's
        lower-case prohibition list does not recognise.
        """
        return (key or "").strip().lower() if isinstance(key, str) else ""

    def get(self, key: str) -> type[BaseConnector]:
        try:
            return self._connectors[self._normalize(key)]
        except KeyError:
            raise ConnectorError(f"Unknown connector: {key}") from None

    def all(self) -> Iterable[type[BaseConnector]]:
        return list(self._connectors.values())

    def keys(self) -> list[str]:
        return sorted(self._connectors)


registry = ConnectorRegistry()
