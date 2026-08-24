"""In-app and email notifications, plus the daily digest.

Email is opt-in and best-effort: a delivery failure is recorded on the row and
never breaks the workflow that triggered it.
"""

from __future__ import annotations

import smtplib
import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import (
    ApplicationStatus,
    MatchDecision,
    NotificationChannel,
    NotificationKind,
    ReviewStatus,
)
from app.core.logging import get_logger
from app.models.application import Application, ReviewTask
from app.models.audit import Notification
from app.models.job import JobMatch
from app.models.user import AgentSettings, User

log = get_logger(__name__)


def create(
    db: Session,
    *,
    user_id: uuid.UUID,
    kind: NotificationKind,
    title: str,
    body: str = "",
    link: str = "",
    data: dict | None = None,
    email: bool | None = None,
) -> Notification:
    notification = Notification(
        user_id=user_id,
        kind=kind.value,
        channel=NotificationChannel.IN_APP.value,
        title=title[:300],
        body=body,
        link=link[:1000],
        data=data or {},
    )
    db.add(notification)
    db.flush()

    if email is None:
        agent_settings = db.execute(
            select(AgentSettings).where(AgentSettings.user_id == user_id)
        ).scalar_one_or_none()
        email = bool(agent_settings and (agent_settings.notify_channels or {}).get("email"))
    if email and settings.notify_email_enabled:
        user = db.get(User, user_id)
        if user:
            _send_email(db, notification, user.email, title, body, link)
    return notification


def _send_email(
    db: Session, notification: Notification, to: str, subject: str, body: str, link: str
) -> None:
    if not settings.smtp_host:
        notification.delivery_error = "SMTP is not configured"
        return
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from
    message["To"] = to
    message.set_content(f"{body}\n\n{settings.frontend_origin}{link}" if link else body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
            if settings.smtp_starttls:
                server.starttls()
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(message)
        notification.channel = NotificationChannel.EMAIL.value
        notification.sent_at = datetime.now(UTC)
    except (smtplib.SMTPException, OSError) as exc:
        notification.delivery_error = str(exc)[:1000]
        log.warning("notification.email_failed", error=str(exc))


def unread_count(db: Session, user_id: uuid.UUID) -> int:
    return int(
        db.execute(
            select(func.count(Notification.id)).where(
                Notification.user_id == user_id, Notification.read_at.is_(None)
            )
        ).scalar_one()
    )


def mark_read(db: Session, user_id: uuid.UUID, notification_ids: list[uuid.UUID] | None) -> int:
    stmt = select(Notification).where(
        Notification.user_id == user_id, Notification.read_at.is_(None)
    )
    if notification_ids:
        stmt = stmt.where(Notification.id.in_(notification_ids))
    rows = list(db.execute(stmt).scalars())
    now = datetime.now(UTC)
    for row in rows:
        row.read_at = now
    return len(rows)


def build_digest(db: Session, user_id: uuid.UUID, *, hours: int = 24) -> dict:
    since = datetime.now(UTC) - timedelta(hours=hours)

    def count(model, *conditions) -> int:
        return int(
            db.execute(
                select(func.count(model.id)).where(model.user_id == user_id, *conditions)
            ).scalar_one()
        )

    top = list(
        db.execute(
            select(JobMatch)
            .where(
                JobMatch.user_id == user_id,
                JobMatch.created_at >= since,
                JobMatch.decision == MatchDecision.SHORTLISTED.value,
            )
            .order_by(JobMatch.score.desc())
            .limit(5)
        ).scalars()
    )
    return {
        "window_hours": hours,
        "new_matches": count(JobMatch, JobMatch.created_at >= since),
        "shortlisted": count(
            JobMatch,
            JobMatch.created_at >= since,
            JobMatch.decision == MatchDecision.SHORTLISTED.value,
        ),
        "awaiting_review": count(ReviewTask, ReviewTask.status == ReviewStatus.OPEN.value),
        "submitted": count(
            Application,
            Application.submitted_at >= since,
            Application.status == ApplicationStatus.SUBMITTED.value,
        ),
        "failed": count(
            Application,
            Application.updated_at >= since,
            Application.status == ApplicationStatus.FAILED.value,
        ),
        "top_matches": [
            {"job_id": str(m.job_id), "score": m.score, "explanation": m.explanation[:400]}
            for m in top
        ],
    }


def send_digest(db: Session, user_id: uuid.UUID, *, hours: int = 24) -> Notification:
    digest = build_digest(db, user_id, hours=hours)
    lines = [
        f"New matches: {digest['new_matches']} ({digest['shortlisted']} shortlisted)",
        f"Awaiting your review: {digest['awaiting_review']}",
        f"Submitted: {digest['submitted']}    Failed: {digest['failed']}",
    ]
    if digest["top_matches"]:
        lines.append("")
        lines.append("Top matches:")
        # `explanation` defaults to "" and "".splitlines() is [], so indexing [0]
        # made an ordinary un-explained match crash the digest with an IndexError.
        lines += [
            f"  {m['score']}: {next(iter(m['explanation'].splitlines()), '(no explanation)')}"
            for m in digest["top_matches"]
        ]
    return create(
        db,
        user_id=user_id,
        kind=NotificationKind.DAILY_DIGEST,
        title=f"Daily digest: {digest['shortlisted']} shortlisted, "
        f"{digest['awaiting_review']} awaiting review",
        body="\n".join(lines),
        link="/dashboard",
        data=digest,
    )
