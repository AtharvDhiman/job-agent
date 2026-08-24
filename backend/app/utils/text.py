"""Text helpers shared by connectors, the normalizer and the ranking engine."""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import UTC, datetime

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_MULTINEWLINE_RE = re.compile(r"\n{3,}")
_LI_OPEN_RE = re.compile(r"<li[^>]*>", re.IGNORECASE)
_BLOCK_TAGS = re.compile(
    r"</?(p|div|br|li|ul|ol|h[1-6]|tr|table|section|article|header|footer)[^>]*>",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#.\-]*")

_LEGAL_SUFFIXES = {
    "inc",
    "inc.",
    "llc",
    "l.l.c.",
    "ltd",
    "ltd.",
    "limited",
    "corp",
    "corp.",
    "corporation",
    "co",
    "co.",
    "gmbh",
    "ag",
    "sa",
    "s.a.",
    "bv",
    "b.v.",
    "nv",
    "plc",
    "pty",
    "ab",
    "oy",
    "as",
    "aps",
    "srl",
    "sarl",
    "spa",
    "kk",
    "pte",
    "the",
}


def strip_html(raw: str | None) -> str:
    """HTML to readable plain text. Deliberately dependency-free and lossy.

    List items keep a leading "- " so downstream requirement extraction can see
    the bullets a job description actually used.
    """
    if not raw:
        return ""
    text = _LI_OPEN_RE.sub("\n- ", raw)
    text = _BLOCK_TAGS.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _MULTINEWLINE_RE.sub("\n\n", text).strip()


def normalize_ws(value: str | None) -> str:
    return _WS_RE.sub(" ", (value or "")).strip()


def fold(value: str | None) -> str:
    """Casefold + strip accents, for comparisons."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold().strip()


def normalize_company(name: str | None) -> str:
    """'Acme, Inc.' and 'ACME Inc' collapse to the same key for dedupe."""
    folded = fold(name)
    folded = re.sub(r"[^a-z0-9 ]+", " ", folded)
    parts = [p for p in folded.split() if p and p not in _LEGAL_SUFFIXES]
    return " ".join(parts)


_TITLE_NOISE = re.compile(
    r"\b(remote|hybrid|on-?site|contract|full[- ]?time|part[- ]?time|urgent|hiring|"
    r"w2|c2c|usa?|uk|emea|apac|multiple locations|f/m/d|m/f/d|m/w/d|h/f)\b",
    re.IGNORECASE,
)


_TITLE_SYNONYMS = (
    (re.compile(r"\bsr\.?(?!\w)"), "senior"),
    (re.compile(r"\bsnr\b"), "senior"),
    (re.compile(r"\bjr\.?(?!\w)"), "junior"),
    (re.compile(r"\bmgr\.?(?!\w)"), "manager"),
    (re.compile(r"\bengr\.?(?!\w)"), "engineer"),
    (re.compile(r"\bdev\b"), "developer"),
    (re.compile(r"\bswe\b"), "software engineer"),
)


def normalize_title(title: str | None) -> str:
    """Collapse cosmetic variants so the same role dedupes across boards.

    Level numerals (II, III) are deliberately KEPT: 'Engineer II' and
    'Engineer III' are different jobs and must not merge.
    """
    folded = fold(title)
    folded = _TITLE_NOISE.sub(" ", folded)
    folded = re.sub(r"[\(\)\[\]\-,/|]+", " ", folded)
    for pattern, replacement in _TITLE_SYNONYMS:
        folded = pattern.sub(replacement, folded)
    return " ".join(folded.split())


def tokenize(text: str | None) -> list[str]:
    return _TOKEN_RE.findall(fold(text))


def parse_datetime(value) -> datetime | None:
    """Accept ISO-8601 strings, epoch seconds and epoch milliseconds."""
    if value in (None, "", 0):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        seconds = value / 1000.0 if value > 1e11 else float(value)
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%d %b %Y", "%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def truncate(text: str, limit: int, suffix: str = "...") -> str:
    text = text or ""
    return text if len(text) <= limit else text[: max(0, limit - len(suffix))].rstrip() + suffix
