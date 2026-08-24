"""Shared vocabulary. These strings appear in the DB, the API and the UI."""

from __future__ import annotations

from enum import StrEnum


class ComplianceTier(StrEnum):
    """How a connector is allowed to READ jobs. See docs/COMPLIANCE.md section 1."""

    PUBLIC_JOB_API = "public_job_api"
    PARTNER_API = "partner_api"
    PUBLIC_FEED = "public_feed"
    CAREERS_PAGE = "careers_page"
    MANUAL_ONLY = "manual_only"


class SubmissionPolicy(StrEnum):
    """How an application may be WRITTEN to a platform. See section 2."""

    PROHIBITED = "prohibited"
    REVIEW_REQUIRED = "review_required"
    ASSISTED_AUTOFILL = "assisted_autofill"
    AUTO_SUBMIT = "auto_submit"


AUTOMATION_POLICIES = {SubmissionPolicy.ASSISTED_AUTOFILL, SubmissionPolicy.AUTO_SUBMIT}


class WorkArrangement(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"
    UNKNOWN = "unknown"


class Seniority(StrEnum):
    INTERN = "intern"
    ENTRY = "entry"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    STAFF = "staff"
    PRINCIPAL = "principal"
    LEAD = "lead"
    MANAGER = "manager"
    DIRECTOR = "director"
    EXECUTIVE = "executive"
    UNKNOWN = "unknown"


SENIORITY_ORDER = [
    Seniority.INTERN,
    Seniority.ENTRY,
    Seniority.JUNIOR,
    Seniority.MID,
    Seniority.SENIOR,
    Seniority.STAFF,
    Seniority.LEAD,
    Seniority.PRINCIPAL,
    Seniority.MANAGER,
    Seniority.DIRECTOR,
    Seniority.EXECUTIVE,
]


class EmploymentType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    TEMPORARY = "temporary"
    UNKNOWN = "unknown"


class MatchDecision(StrEnum):
    SHORTLISTED = "shortlisted"
    BELOW_THRESHOLD = "below_threshold"
    REJECTED_HARD_FILTER = "rejected_hard_filter"
    EXCLUDED_COMPANY = "excluded_company"
    EXCLUDED_KEYWORD = "excluded_keyword"
    STALE_POSTING = "stale_posting"
    DUPLICATE = "duplicate"


class ApplicationStatus(StrEnum):
    DRAFTING = "drafting"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED_BY_POLICY = "blocked_by_policy"


class PipelineStage(StrEnum):
    SAVED = "saved"
    APPLIED = "applied"
    SCREENING = "screening"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    CLOSED = "closed"


class ReviewReason(StrEnum):
    BELOW_AUTO_SUBMIT_THRESHOLD = "below_auto_submit_threshold"
    PLATFORM_NOT_AUTHORIZED = "platform_not_authorized"
    PLATFORM_PROHIBITS_AUTOMATION = "platform_prohibits_automation"
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    CAPTCHA_DETECTED = "captcha_detected"
    LOGIN_REQUIRED = "login_required"
    BOT_PROTECTION_DETECTED = "bot_protection_detected"
    ROBOTS_DISALLOWED = "robots_disallowed"
    UNANSWERABLE_QUESTION = "unanswerable_question"
    FREE_TEXT_QUESTION = "free_text_question"
    MISSING_VERIFIED_FACT = "missing_verified_fact"
    FACT_GUARD_FLAGGED = "fact_guard_flagged"
    VALIDATION_FAILED = "validation_failed"
    MISSING_ATTACHMENT = "missing_attachment"
    DAILY_LIMIT_REACHED = "daily_limit_reached"
    AUTOMATION_DISABLED = "automation_disabled"
    SUBMISSION_ERROR = "submission_error"
    MANUAL_REQUEST = "manual_request"


class ReviewStatus(StrEnum):
    OPEN = "open"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class DocumentKind(StrEnum):
    RESUME_SOURCE = "resume_source"
    RESUME_GENERATED = "resume_generated"
    COVER_LETTER_SOURCE = "cover_letter_source"
    COVER_LETTER_GENERATED = "cover_letter_generated"
    CERTIFICATION = "certification"
    PORTFOLIO = "portfolio"
    TRANSCRIPT = "transcript"
    OTHER = "other"


class FactCategory(StrEnum):
    EMPLOYMENT = "employment"
    EDUCATION = "education"
    CERTIFICATION = "certification"
    SKILL = "skill"
    PROJECT = "project"
    ACHIEVEMENT = "achievement"
    LANGUAGE = "language"
    WORK_AUTHORIZATION = "work_authorization"
    COMPENSATION = "compensation"
    REFERENCE = "reference"
    LINK = "link"
    PERSONAL = "personal"
    SCREENING_ANSWER = "screening_answer"


class QuestionType(StrEnum):
    SHORT_TEXT = "short_text"
    LONG_TEXT = "long_text"
    BOOLEAN = "boolean"
    SINGLE_SELECT = "single_select"
    MULTI_SELECT = "multi_select"
    NUMBER = "number"
    DATE = "date"
    FILE = "file"
    EEO = "eeo"
    UNKNOWN = "unknown"


class NotificationKind(StrEnum):
    HIGH_MATCH_JOB = "high_match_job"
    REVIEW_REQUIRED = "review_required"
    SUBMISSION_SUCCEEDED = "submission_succeeded"
    SUBMISSION_FAILED = "submission_failed"
    EMPLOYER_REPLY = "employer_reply"
    DAILY_DIGEST = "daily_digest"
    SYSTEM_ALERT = "system_alert"


class NotificationChannel(StrEnum):
    IN_APP = "in_app"
    EMAIL = "email"


class AuditAction(StrEnum):
    USER_LOGIN = "user.login"
    USER_LOGIN_FAILED = "user.login_failed"
    PROFILE_UPDATED = "profile.updated"
    FACT_CREATED = "fact.created"
    FACT_VERIFIED = "fact.verified"
    FACT_DELETED = "fact.deleted"
    DOCUMENT_UPLOADED = "document.uploaded"
    DOCUMENT_GENERATED = "document.generated"
    DISCOVERY_RUN = "discovery.run"
    JOB_INGESTED = "job.ingested"
    MATCH_SCORED = "match.scored"
    APPLICATION_DRAFTED = "application.drafted"
    APPLICATION_APPROVED = "application.approved"
    APPLICATION_REJECTED = "application.rejected"
    APPLICATION_SUBMITTED = "application.submitted"
    APPLICATION_FAILED = "application.failed"
    REVIEW_CREATED = "review.created"
    REVIEW_RESOLVED = "review.resolved"
    AUTHORIZATION_GRANTED = "authorization.granted"
    AUTHORIZATION_REVOKED = "authorization.revoked"
    AUTOMATION_PAUSED = "automation.paused"
    AUTOMATION_RESUMED = "automation.resumed"
    SETTINGS_UPDATED = "settings.updated"
    DATA_EXPORTED = "data.exported"
    DATA_ERASED = "data.erased"
    POLICY_BLOCK = "policy.block"
