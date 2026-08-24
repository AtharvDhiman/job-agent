"""Connector package.

Importing this module registers every shipped connector. Adding a platform is:
  1. create a module here subclassing BaseConnector,
  2. declare compliance_tier + submission_policy_default + policy_note,
  3. decorate with @registry.register,
  4. import it below.
The registry raises if a connector omits its compliance declaration.
"""

from app.connectors import (  # noqa: F401  (import for side-effect registration)
    ashby,
    careers_page,
    feeds,
    greenhouse,
    lever,
    partner,
    smartrecruiters,
    workable,
)
from app.connectors.base import (  # noqa: F401
    BaseConnector,
    BlockedByPolicyError,
    ConnectorError,
    FetchResult,
    RawJob,
    SourceSpec,
    registry,
)
from app.connectors.http import PoliteClient  # noqa: F401

__all__ = [
    "BaseConnector",
    "BlockedByPolicyError",
    "ConnectorError",
    "FetchResult",
    "PoliteClient",
    "RawJob",
    "SourceSpec",
    "registry",
]
