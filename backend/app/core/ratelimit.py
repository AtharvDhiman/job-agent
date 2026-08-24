"""Fixed-window rate limiting.

Backed by Redis when available so it works across workers; falls back to an
in-process counter so local development and tests do not need Redis.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

_local_counters: dict[str, list[float]] = defaultdict(list)
_lock = threading.Lock()
_redis_client = None
#: Monotonic timestamp until which we stop trying Redis. A single blip used to
#: latch `_redis_failed = True` for the life of the process, silently and
#: permanently downgrading a multi-worker deployment to per-process counters --
#: which multiplies the real limit by the worker count.
_redis_retry_after = 0.0
REDIS_RETRY_SECONDS = 30.0


def _mark_redis_down(exc: Exception) -> None:
    global _redis_client, _redis_retry_after
    _redis_client = None
    _redis_retry_after = time.monotonic() + REDIS_RETRY_SECONDS
    log.warning("ratelimit.redis_unavailable", error=str(exc), retry_in_seconds=REDIS_RETRY_SECONDS)


def _redis():
    global _redis_client
    if not settings.redis_url or time.monotonic() < _redis_retry_after:
        return None
    if _redis_client is None:
        try:
            import redis

            client = redis.Redis.from_url(settings.redis_url, socket_timeout=1)
            client.ping()
            _redis_client = client
        except Exception as exc:  # noqa: BLE001 - degrade, never fail the request
            _mark_redis_down(exc)
            return None
    return _redis_client


def check(key: str, *, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
    """Returns (allowed, remaining)."""
    if not settings.rate_limit_enabled or limit <= 0:
        return True, limit

    client = _redis()
    if client is not None:
        try:
            bucket = f"rl:{key}:{int(time.time() // window_seconds)}"
            count = client.incr(bucket)
            if count == 1:
                client.expire(bucket, window_seconds)
            return count <= limit, max(0, limit - int(count))
        except Exception as exc:  # noqa: BLE001
            log.warning("ratelimit.redis_error", error=str(exc))

    now = time.monotonic()
    with _lock:
        hits = [t for t in _local_counters[key] if now - t < window_seconds]
        hits.append(now)
        _local_counters[key] = hits
        return len(hits) <= limit, max(0, limit - len(hits))


def reset() -> None:
    """Test helper."""
    with _lock:
        _local_counters.clear()
