"""Generic RSS / Atom job feed.

Discovery: PUBLIC_FEED. Feeds exist to be syndicated; we still honour robots.txt
and conditional requests. Submission: n/a, feeds link out to the employer.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree  # noqa: S405 - ParseError only, parsing uses defusedxml

import defusedxml.ElementTree as SafeElementTree

from app.connectors.base import (
    BaseConnector,
    ConnectorError,
    FetchResult,
    RawJob,
    SourceSpec,
    registry,
)
from app.core.enums import ComplianceTier, SubmissionPolicy
from app.utils.text import normalize_ws, parse_datetime, strip_html

_NS = {"atom": "http://www.w3.org/2005/Atom", "content": "http://purl.org/rss/1.0/modules/content/"}


@registry.register
class RSSFeedConnector(BaseConnector):
    key = "rss"
    display_name = "RSS / Atom feed"
    compliance_tier = ComplianceTier.PUBLIC_FEED
    submission_policy_default = SubmissionPolicy.REVIEW_REQUIRED
    policy_note = (
        "Feeds are published for syndication. robots.txt is still checked and "
        "requests are conditional. Applications always link out for your review."
    )
    direct_employer = False
    identifier_label = "Feed URL"
    identifier_help = "Full https:// URL of an RSS or Atom job feed."

    def fetch(self, spec: SourceSpec, *, etag: str = "") -> FetchResult:
        url = spec.identifier.strip()
        if not url.startswith("https://"):
            raise ConnectorError("Feed URL must be https://")
        resp = self.http.get(url, etag=etag)
        if resp.status_code == 304:
            return FetchResult(jobs=[], etag=etag, notes=["not modified"])
        try:
            # A feed is third-party XML we did not author. defusedxml refuses
            # entity expansion and external entity references, so a hostile or
            # merely broken feed cannot turn into a billion-laughs DoS or an
            # XXE read of the worker's filesystem.
            root = SafeElementTree.fromstring(resp.text)
        except ElementTree.ParseError as exc:
            raise ConnectorError(f"Feed {url} is not valid XML: {exc}") from exc

        entries = root.findall(".//item") or root.findall(".//atom:entry", _NS)
        company_default = spec.display_name or spec.config.get("company", "")
        jobs = []
        for entry in entries:
            title = _text(entry, "title") or _text(entry, "atom:title")
            link = _text(entry, "link") or _attr(entry, "atom:link", "href")
            if not title or not link:
                continue
            body = (
                _text(entry, "content:encoded")
                or _text(entry, "description")
                or _text(entry, "atom:summary")
                or ""
            )
            guid = _text(entry, "guid") or _text(entry, "atom:id") or link
            jobs.append(
                RawJob(
                    external_id=guid[:280],
                    title=normalize_ws(title),
                    company=_text(entry, "author") or company_default or _host(link),
                    source_url=link,
                    apply_url=link,
                    description_html=body,
                    description_text=strip_html(body),
                    location_raw=_text(entry, "location") or spec.config.get("location", ""),
                    posted_at=parse_datetime(
                        _text(entry, "pubDate")
                        or _text(entry, "atom:published")
                        or _text(entry, "atom:updated")
                    ),
                    is_direct_employer=bool(spec.config.get("direct_employer", False)),
                    raw={"feed": url},
                )
            )
        return FetchResult(jobs=jobs, etag=resp.headers.get("ETag", ""))


def _text(node, path: str) -> str:
    found = node.find(path, _NS) if ":" in path else node.find(path)
    return normalize_ws(found.text) if found is not None and found.text else ""


def _attr(node, path: str, attr: str) -> str:
    found = node.find(path, _NS)
    return (found.get(attr) or "") if found is not None else ""


def _host(url: str) -> str:
    match = re.match(r"https?://([^/]+)", url or "")
    return match.group(1) if match else ""
