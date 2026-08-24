"""Import every model so Alembic autogenerate and Base.metadata see them all."""

from app.models.application import (  # noqa: F401
    Application,
    ApplicationAnswer,
    ApplicationDocument,
    ReviewTask,
    SubmissionAttempt,
)
from app.models.audit import AuditLog, DailyCounter, Notification  # noqa: F401
from app.models.job import Job, JobMatch, JobSourceSubscription  # noqa: F401
from app.models.profile import CandidateProfile, CareerFact, Document  # noqa: F401
from app.models.user import AgentSettings, PlatformAuthorization, RefreshToken, User  # noqa: F401

__all__ = [
    "AgentSettings",
    "Application",
    "ApplicationAnswer",
    "ApplicationDocument",
    "AuditLog",
    "CandidateProfile",
    "CareerFact",
    "DailyCounter",
    "Document",
    "Job",
    "JobMatch",
    "JobSourceSubscription",
    "Notification",
    "PlatformAuthorization",
    "RefreshToken",
    "ReviewTask",
    "SubmissionAttempt",
    "User",
]
