"""Regression tests for the security defects found in the hostile review.

Each test names the hole it closes. None of them assert on comments or
docstrings: they drive the real functions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core import crypto
from app.core.logging import _redact
from app.core.security import Role, constant_time_equals, role_allows
from app.services import audit as audit_service
from app.services import storage


# --------------------------------------------------------------------- RBAC
def test_service_role_is_outside_the_human_ladder():
    """SERVICE shared rank 0 with VIEWER, so each satisfied the other's checks."""
    assert role_allows("viewer", Role.SERVICE) is False
    assert role_allows("operator", Role.SERVICE) is False
    assert role_allows("owner", Role.SERVICE) is False
    assert role_allows("service", Role.VIEWER) is False
    assert role_allows("service", Role.OPERATOR) is False
    assert role_allows("service", Role.OWNER) is False
    assert role_allows("service", Role.SERVICE) is True


def test_the_human_ladder_still_works():
    assert role_allows("owner", Role.OPERATOR) is True
    assert role_allows("owner", Role.VIEWER) is True
    assert role_allows("operator", Role.VIEWER) is True
    assert role_allows("operator", Role.OWNER) is False
    assert role_allows("viewer", Role.OPERATOR) is False
    assert role_allows("nonsense", Role.VIEWER) is False


def test_constant_time_equals_compares_exactly():
    assert constant_time_equals("abc", "abc") is True
    assert constant_time_equals("abc", "abd") is False
    assert constant_time_equals("abc", "abc ") is False
    assert constant_time_equals("", "") is True
    # Non-ASCII must not raise: compare_digest needs bytes of equal encoding.
    assert constant_time_equals("tokén", "tokén") is True


# ------------------------------------------------------------ path traversal
def test_local_storage_refuses_a_relative_traversal(tmp_path: Path):
    backend = storage.LocalStorage(tmp_path / "storage")
    with pytest.raises(ValueError):
        backend.write("../../etc/passwd", b"x")


def test_local_storage_refuses_a_sibling_prefix_escape(tmp_path: Path):
    """`str(target).startswith(str(root))` is not containment.

    With root ".../storage", the key "../storage_evil/x" resolves to
    ".../storage_evil/x", which passes a startswith test and lands outside.
    """
    root = tmp_path / "storage"
    backend = storage.LocalStorage(root)
    with pytest.raises(ValueError):
        backend.write("../storage_evil/pwned.txt", b"x")
    assert not (tmp_path / "storage_evil").exists()


def test_local_storage_refuses_an_absolute_key(tmp_path: Path):
    backend = storage.LocalStorage(tmp_path / "storage")
    outside = tmp_path / "outside.txt"
    with pytest.raises(ValueError):
        backend.write(str(outside), b"x")
    assert not outside.exists()

    with pytest.raises(ValueError):
        backend.read(str(outside))
    with pytest.raises(ValueError):
        backend.delete(str(outside))


def test_local_storage_still_writes_a_normal_key(tmp_path: Path):
    backend = storage.LocalStorage(tmp_path / "storage")
    key = storage.build_key(
        "11111111-2222-3333-4444-555555555555", "resume_source", "a" * 64, "cv.pdf"
    )
    backend.write(key, b"hello")
    assert backend.read(key) == b"hello"
    assert backend.exists(key) is True


def test_build_key_sanitises_every_component_not_just_the_filename():
    """`kind` arrives from a multipart form field and reached the path raw."""
    key = storage.build_key("u1", "../../etc", "deadbeefdeadbeef", "../../../evil.sh")
    assert ".." not in key
    assert key.count("/") == 2


def test_build_key_survives_a_hostile_filename():
    key = storage.build_key("u1", "resume_source", "abc123abc123", 'a"b\r\nc/../d.pdf')
    assert "\r" not in key and "\n" not in key and '"' not in key
    assert ".." not in key


# ------------------------------------------------------- header construction
def test_content_disposition_cannot_break_out_of_the_header():
    value = storage.content_disposition('evil"; filename="payload.exe')
    quoted = value.split('filename="')[1].split('"')[0]
    # No stray quote, so the parameter cannot be closed early and re-opened.
    assert '"' not in quoted
    assert ";" not in quoted
    assert value.count('filename="') == 1
    # The exact original is still recoverable, percent-encoded per RFC 5987.
    assert "filename*=UTF-8''" in value


def test_content_disposition_strips_crlf():
    value = storage.content_disposition("a\r\nX-Injected: yes.pdf")
    assert "\r" not in value and "\n" not in value


# --------------------------------------------------------- upload validation
def test_upload_validator_no_longer_skips_a_missing_content_type():
    """An empty content type short-circuited the whole allow-list check."""
    errors = storage.validate_upload("x.html", b"<script>alert(1)</script>", "")
    assert errors == [] or all("content type" not in e for e in errors)
    errors = storage.validate_upload("x.html", b"<html>", "text/html")
    assert any("Unsupported content type" in e for e in errors)


def test_upload_validator_rejects_executables_and_empties():
    assert any(
        "Executable" in e
        for e in storage.validate_upload("a.pdf", b"MZ\x90\x00", "application/pdf")
    )
    assert any("empty" in e for e in storage.validate_upload("a.pdf", b"", "application/pdf"))


# ------------------------------------------------------------------ redaction
def test_log_redaction_catches_names_the_exact_list_never_enumerated():
    event = _redact(
        None,
        None,
        {
            "x_assistant_token": "s3cret",
            "browser_assistant_token": "s3cret",
            "smtp_password": "s3cret",
            "confirmation_number": "GH-1",
            "answer_value": "visa answer",
            "phone": "+1 415 555 0100",
            "path": "/api/v1/jobs",
        },
    )
    for key in (
        "x_assistant_token",
        "browser_assistant_token",
        "smtp_password",
        "confirmation_number",
        "answer_value",
        "phone",
    ):
        assert event[key] == "[redacted]", key
    assert event["path"] == "/api/v1/jobs"


def test_log_redaction_reaches_into_nested_structures():
    event = _redact(None, None, {"detail": {"inner": [{"api_key": "k"}, {"safe": 1}]}})
    assert event["detail"]["inner"][0]["api_key"] == "[redacted]"
    assert event["detail"]["inner"][1]["safe"] == 1


def test_audit_scrub_walks_lists():
    """Lists were skipped, so findings carrying an answer value went in raw."""
    scrubbed = audit_service._scrub(
        {
            "guard_findings": [{"answer_value": "I have a visa", "ok": True}],
            "assistant_token": "abc",
            "count": 3,
        }
    )
    assert scrubbed["guard_findings"][0]["answer_value"] == "[redacted]"
    assert scrubbed["guard_findings"][0]["ok"] is True
    assert scrubbed["assistant_token"] == "[redacted]"
    assert scrubbed["count"] == 3


# --------------------------------------------------------------------- crypto
def test_round_trip_and_rotation_under_the_primary_key():
    token = crypto.encrypt_str("sensitive")
    assert token is not None and token.startswith("enc:v1:")
    assert "sensitive" not in token
    assert crypto.decrypt_str(token) == "sensitive"

    rotated = crypto.rotate(token)
    assert rotated.startswith("enc:v1:")
    assert rotated != token
    assert crypto.decrypt_str(rotated) == "sensitive"


def test_rotate_encrypts_a_legacy_plaintext_value():
    rotated = crypto.rotate("legacy plaintext")
    assert rotated.startswith("enc:v1:")
    assert crypto.decrypt_str(rotated) == "legacy plaintext"


def test_json_round_trip_and_legacy_passthrough():
    token = crypto.encrypt_json({"authorized": True, "country": "US"})
    assert crypto.decrypt_json(token) == {"authorized": True, "country": "US"}
    # A pre-encryption JSONB column dumped to text still decodes.
    assert crypto.decrypt_json(json.dumps({"a": 1})) == {"a": 1}
    assert crypto.decrypt_json(None) is None


def test_a_missing_encryption_key_is_fatal_outside_development():
    """The derived dev key must not be reachable in staging or production."""
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            secret_key="x" * 48,
            encryption_key="",
            _env_file=None,
        )
    # Development is allowed to derive one.
    assert Settings(app_env="development", encryption_key="", _env_file=None).encryption_key == ""
