"""Polite HTTP client for connectors.

Enforces: identifying User-Agent, per-host rate limit, robots.txt, conditional
requests, bounded retries, and a hard refusal to touch anything that looks like
a bot-check or login wall.
"""

from __future__ import annotations

import time
import urllib.robotparser
from dataclasses import dataclass
from threading import Lock
from urllib.parse import urlparse

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.connectors.base import BlockedByPolicyError, ConnectorError
from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

BOT_WALL_MARKERS = (
    "captcha",
    "recaptcha",
    "hcaptcha",
    "cf-turnstile",
    "datadome",
    "perimeterx",
    "px-captcha",
    "kasada",
    "are you a robot",
    "enable javascript and cookies to continue",
    "checking your browser before accessing",
)
LOGIN_WALL_MARKERS = ("sign in to continue", "please log in to view", "authwall")


@dataclass
class _HostBudget:
    min_interval: float
    last_call: float = 0.0


class PoliteClient:
    """Thin wrapper over httpx.Client with the safety rules baked in."""

    def __init__(self, *, user_agent: str | None = None, timeout: float | None = None):
        self.user_agent = user_agent or settings.discovery_user_agent
        self._client = httpx.Client(
            timeout=timeout or settings.discovery_http_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json, text/html;q=0.8, */*;q=0.5",
            },
        )
        self._budgets: dict[str, _HostBudget] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._lock = Lock()

    # -- lifecycle -------------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- policy ----------------------------------------------------------
    def _throttle(self, host: str) -> None:
        rps = max(settings.discovery_per_host_rps, 0.05)
        with self._lock:
            budget = self._budgets.setdefault(host, _HostBudget(min_interval=1.0 / rps))
            wait = budget.min_interval - (time.monotonic() - budget.last_call)
            if wait > 0:
                time.sleep(min(wait, 10.0))
            budget.last_call = time.monotonic()

    def robots_allows(self, url: str) -> bool:
        if not settings.respect_robots_txt:
            return True
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(f"{origin}/robots.txt")
            try:
                resp = self._client.get(f"{origin}/robots.txt", timeout=10.0)
                if resp.status_code >= 400:
                    parser = None  # no robots.txt published -> allowed
                else:
                    parser.parse(resp.text.splitlines())
            except httpx.HTTPError:
                parser = None
            self._robots[origin] = parser
        parser = self._robots[origin]
        if parser is None:
            return True
        return parser.can_fetch(self.user_agent, url)

    @staticmethod
    def assert_no_bot_wall(url: str, body: str) -> None:
        lowered = body[:20000].lower()
        for marker in BOT_WALL_MARKERS:
            if marker in lowered:
                raise BlockedByPolicyError(
                    f"{url} presents a bot-check ({marker}). Stopping: we never attempt to "
                    "bypass bot detection. This source needs manual review."
                )
        for marker in LOGIN_WALL_MARKERS:
            if marker in lowered:
                raise BlockedByPolicyError(
                    f"{url} requires a login. Stopping: the agent holds no credentials."
                )

    # -- requests --------------------------------------------------------
    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=12),
        reraise=True,
    )
    def _request(self, method: str, url: str, **kw) -> httpx.Response:
        return self._client.request(method, url, **kw)

    def get(self, url: str, *, etag: str = "", check_robots: bool = True, **kw) -> httpx.Response:
        if check_robots and not self.robots_allows(url):
            raise BlockedByPolicyError(
                f"robots.txt disallows {url} for {self.user_agent}. Not fetching."
            )
        self._throttle(urlparse(url).netloc)
        headers = dict(kw.pop("headers", {}))
        if etag:
            headers["If-None-Match"] = etag
        try:
            resp = self._request("GET", url, headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"GET {url} failed: {exc}") from exc

        if resp.status_code == 304:
            return resp
        if resp.status_code in (401, 403):
            raise BlockedByPolicyError(
                f"GET {url} returned {resp.status_code}. The source is gated; "
                "we do not attempt to work around access controls."
            )
        if resp.status_code == 429:
            raise ConnectorError(f"GET {url} rate-limited (429). Backing off.")
        if resp.status_code >= 400:
            raise ConnectorError(f"GET {url} returned HTTP {resp.status_code}")
        ctype = resp.headers.get("content-type", "")
        if "html" in ctype:
            self.assert_no_bot_wall(url, resp.text)
        return resp

    def get_json(self, url: str, *, etag: str = "", **kw):
        resp = self.get(url, etag=etag, **kw)
        if resp.status_code == 304:
            return None, resp.headers.get("ETag", etag)
        try:
            return resp.json(), resp.headers.get("ETag", "")
        except ValueError as exc:
            raise ConnectorError(f"GET {url} did not return JSON: {exc}") from exc
