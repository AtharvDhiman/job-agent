"""Portable column types.

JSONB on PostgreSQL, JSON on SQLite (tests).  Encrypted variants transparently
Fernet-wrap the value so it is never at rest in the clear.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import JSON, String, Text, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from app.core import crypto


class JSONType(TypeDecorator):
    """JSONB where available, JSON otherwise."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class GUID(TypeDecorator):
    """UUID column that degrades to CHAR(36) on SQLite."""

    impl = String(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PGUUID(as_uuid=True))
        return dialect.type_descriptor(String(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        return value if dialect.name == "postgresql" else str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class EncryptedString(TypeDecorator):
    """Text column encrypted at rest with the application key ring."""

    impl = Text
    cache_ok = False

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        return crypto.encrypt_str(value)

    def process_result_value(self, value: str | None, dialect) -> str | None:
        return crypto.decrypt_str(value)


class EncryptedJSON(TypeDecorator):
    """Arbitrary JSON encrypted at rest. Not queryable by design."""

    impl = Text
    cache_ok = False

    def process_bind_param(self, value: Any, dialect) -> str | None:
        return crypto.encrypt_json(value)

    def process_result_value(self, value: str | None, dialect) -> Any:
        return crypto.decrypt_json(value)
