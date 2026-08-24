"""Envelope encryption for sensitive columns.

Fernet (AES-128-CBC + HMAC-SHA256) with key rotation: new values are written
with the primary key, old values decrypt against any key in the ring.  See
docs/COMPLIANCE.md section 5.
"""

from __future__ import annotations

import base64
import hashlib
import json
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import settings

_PREFIX = "enc:v1:"


def _derive_dev_key() -> str:
    """Deterministic throwaway key so local dev and tests work without setup.

    Never used when APP_ENV is staging or production: the guard below makes a
    missing ENCRYPTION_KEY fatal there.
    """
    digest = hashlib.sha256(f"dev-key::{settings.secret_key}".encode()).digest()
    return base64.urlsafe_b64encode(digest).decode()


@lru_cache
def _fernet() -> MultiFernet:
    primary = settings.encryption_key.strip()
    if not primary:
        if settings.is_production:
            raise RuntimeError("ENCRYPTION_KEY is required when APP_ENV is staging/production")
        primary = _derive_dev_key()
    keys = [primary]
    keys += [k.strip() for k in settings.encryption_key_previous.split(",") if k.strip()]
    try:
        return MultiFernet([Fernet(k.encode()) for k in keys])
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"Invalid ENCRYPTION_KEY material: {exc}") from exc


def encrypt_str(plaintext: str | None) -> str | None:
    if plaintext is None:
        return None
    return _PREFIX + _fernet().encrypt(plaintext.encode()).decode()


def decrypt_str(ciphertext: str | None) -> str | None:
    if ciphertext is None:
        return None
    if not ciphertext.startswith(_PREFIX):
        # Value predates encryption (e.g. an imported fixture). Return as-is so a
        # migration can re-encrypt it rather than crashing every read.
        return ciphertext
    try:
        return _fernet().decrypt(ciphertext[len(_PREFIX) :].encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError(
            "Could not decrypt a stored value. The ENCRYPTION_KEY changed without "
            "rotation: put the old key in ENCRYPTION_KEY_PREVIOUS."
        ) from exc


def encrypt_json(value: Any) -> str | None:
    if value is None:
        return None
    return encrypt_str(json.dumps(value, separators=(",", ":"), sort_keys=True, default=str))


def decrypt_json(ciphertext: str | None) -> Any:
    raw = decrypt_str(ciphertext)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def rotate(ciphertext: str) -> str:
    """Re-encrypt an existing token under the primary key."""
    if not ciphertext.startswith(_PREFIX):
        return encrypt_str(ciphertext) or ciphertext
    token = _fernet().rotate(ciphertext[len(_PREFIX) :].encode()).decode()
    return _PREFIX + token
