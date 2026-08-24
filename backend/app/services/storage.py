"""Content-addressed file storage. Local filesystem by default, S3 optional."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import quote

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
#: Two or more dots in a row. Separators are already replaced above, so ".." can
#: no longer traverse, but leaving it in a key is a trap for the next backend
#: that joins keys differently. Collapse it; single dots (extensions) survive.
_DOT_RUN = re.compile(r"\.{2,}")

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "text/plain",
    "text/markdown",
    "image/png",
    "image/jpeg",
    "application/octet-stream",
}


def safe_filename(name: str) -> str:
    cleaned = _DOT_RUN.sub("_", _SAFE.sub("_", (name or "file").strip()))[:180]
    return cleaned.strip("._-") or "file"


def sha256_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def build_key(user_id, kind: str, digest: str, filename: str) -> str:
    """Build a storage key. EVERY component is sanitised, not just the filename.

    `kind` reaches this function straight from a multipart form field, so a value
    like "../../etc" would otherwise walk out of the user's prefix.
    """
    return "/".join(
        (
            safe_filename(str(user_id)),
            safe_filename(kind),
            f"{safe_filename(digest)[:12]}-{safe_filename(filename)}",
        )
    )


class LocalStorage:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or settings.storage_root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        """Resolve a key inside the root, or refuse.

        A string `startswith` test is not containment: with root "/srv/storage",
        the key "../storage_evil/x" resolves to "/srv/storage_evil/x", which
        starts with "/srv/storage" and used to be accepted. Ask pathlib whether
        the resolved target is genuinely relative to the resolved root instead.
        """
        root = self.root.resolve()
        target = (root / key).resolve()
        if target != root and root not in target.parents:
            raise ValueError("Refusing to touch a path outside the storage root")
        return target

    def write(self, key: str, content: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return key

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    def iter_keys(self, prefix: str = ""):
        base = self._path(prefix) if prefix else self.root
        if not base.exists():
            return
        for path in base.rglob("*"):
            if path.is_file():
                yield str(path.relative_to(self.root)).replace("\\", "/")


class S3Storage:  # pragma: no cover - exercised only when configured
    def __init__(self):
        import boto3

        self.bucket = settings.s3_bucket
        if not self.bucket:
            raise RuntimeError("STORAGE_BACKEND=s3 requires S3_BUCKET")
        self.client = boto3.client(
            "s3",
            region_name=settings.s3_region or None,
            endpoint_url=settings.s3_endpoint_url or None,
        )

    def write(self, key: str, content: bytes) -> str:
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=content, ServerSideEncryption="AES256"
        )
        return key

    def read(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def iter_keys(self, prefix: str = ""):
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                yield item["Key"]


_backend = None


def get_storage():
    global _backend
    if _backend is None:
        _backend = S3Storage() if settings.storage_backend == "s3" else LocalStorage()
    return _backend


def content_disposition(filename: str, disposition: str = "attachment") -> str:
    """A Content-Disposition value that cannot break out of the header.

    `filename` is user-controlled (it is whatever the browser sent at upload
    time). A quote closes the parameter early and a CR/LF splits the response,
    so the quoted form is built from sanitised characters only and the exact
    original is offered separately via RFC 5987 percent-encoding.
    """
    cleaned = safe_filename(filename)
    encoded = quote(filename or "file", safe="")
    return f"{disposition}; filename=\"{cleaned}\"; filename*=UTF-8''{encoded}"


def validate_upload(filename: str, content: bytes, content_type: str) -> list[str]:
    errors: list[str] = []
    if not content:
        errors.append("File is empty")
    if len(content) > MAX_UPLOAD_BYTES:
        errors.append(f"File exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)}MB")
    # An absent content type used to skip this check entirely, so any client that
    # simply omitted the header could upload anything.
    declared = (content_type or "application/octet-stream").split(";")[0].strip().lower()
    if declared not in ALLOWED_CONTENT_TYPES:
        errors.append(f"Unsupported content type: {content_type}")
    if content[:2] == b"MZ" or content[:4] == b"\x7fELF":
        errors.append("Executable uploads are rejected")
    return errors
