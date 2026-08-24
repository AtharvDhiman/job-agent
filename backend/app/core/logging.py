"""Structured logging. One JSON object per line in production, pretty in dev.

A request-id is bound to a contextvar by the middleware so every log line and
audit row emitted while serving a request can be correlated.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

import structlog

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
actor_id_var: ContextVar[str | None] = ContextVar("actor_id", default=None)

#: Exact field names that must never be logged.
_SENSITIVE_KEYS = {
    "password",
    "hashed_password",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "anthropic_api_key",
    "authorization",
    "secret",
    "secret_key",
    "encryption_key",
    "smtp_password",
    "ssn",
    "phone",
    "address",
    "answer",
    "answer_value",
    "confirmation_number",
    "acknowledgement",
    "acknowledgement_text",
    "extracted_text",
    "work_authorization",
}

#: Substrings that make ANY field name sensitive, so a new call site cannot leak
#: a value simply by inventing a field name the exact set does not list
#: (x_assistant_token, browser_assistant_token, partner_api_token, ...).
_SENSITIVE_FRAGMENTS = (
    "password",
    "token",
    "secret",
    "api_key",
    "apikey",
    "credential",
    "authorization",
)

_REDACTED = "[redacted]"


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    if lowered in _SENSITIVE_KEYS:
        return True
    return any(fragment in lowered for fragment in _SENSITIVE_FRAGMENTS)


def _redact_value(value):
    if isinstance(value, dict):
        return {
            k: (_REDACTED if _is_sensitive(str(k)) else _redact_value(v)) for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_value(v) for v in value]
    return value


def _redact(_logger, _name, event_dict):
    for key in list(event_dict):
        if _is_sensitive(str(key)):
            event_dict[key] = _REDACTED
        else:
            event_dict[key] = _redact_value(event_dict[key])
    return event_dict


def _inject_context(_logger, _name, event_dict):
    rid = request_id_var.get()
    if rid:
        event_dict.setdefault("request_id", rid)
    aid = actor_id_var.get()
    if aid:
        event_dict.setdefault("actor_id", aid)
    return event_dict


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    renderer = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=True)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _inject_context,
            _redact,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "jobagent"):
    return structlog.get_logger(name)
