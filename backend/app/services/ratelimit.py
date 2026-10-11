"""In-process rate limiting for the unauthenticated auth surface (login, centre registration, magic-link exchange).

A fixed-size sliding window per key (client IP or e-mail) kept in memory: good enough for a single API process
(the MVP deployment); a reverse proxy ``limit_req`` or a shared store is the next step when the API is scaled
out. Disabled automatically under ``APP_ENV=test`` and by ``RATE_LIMIT_ENABLED=false``; tests that exercise the
limiter enable it explicitly.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import HTTPException, Request, status

from app.config import get_settings
from app.logging import get_logger

log = get_logger("app.services.ratelimit")

RETRY_TEXT = "Слишком много попыток. Попробуйте позже."


def rate_limiting_enabled() -> bool:
    """``RATE_LIMIT_ENABLED`` (default: on, except under ``APP_ENV=test``)."""
    raw = os.environ.get("RATE_LIMIT_ENABLED")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return get_settings().app_env != "test"


class SlidingWindowLimiter:
    """``limit`` hits per ``window_sec`` for each key; thread-safe, bounded memory (idle keys are pruned)."""

    def __init__(self, limit: int, window_sec: float) -> None:
        self.limit = limit
        self.window_sec = window_sec
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._last_prune = 0.0

    def check(self, key: str, *, now: float | None = None) -> float | None:
        """Record a hit for ``key``; return ``None`` when allowed, else seconds until the next slot frees up."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if now - self._last_prune > self.window_sec:
                self._prune(now)
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= self.window_sec:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(0.0, self.window_sec - (now - hits[0]))
            hits.append(now)
            return None

    def reset(self) -> None:
        """Forget every key (tests)."""
        with self._lock:
            self._hits.clear()

    def _prune(self, now: float) -> None:
        self._last_prune = now
        for key in [k for k, hits in self._hits.items() if not hits or now - hits[-1] >= self.window_sec]:
            del self._hits[key]


def client_ip(request: Request) -> str:
    """Client address: first ``X-Forwarded-For`` entry (set by the reverse proxy) or the socket peer."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _too_many(retry_after: float, scope: str, key_kind: str) -> HTTPException:
    log.warning("rate_limited", scope=scope, key_kind=key_kind)
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=RETRY_TEXT,
        headers={"Retry-After": str(max(1, int(retry_after) + 1))},
    )


def rate_limited(
    scope: str,
    *,
    per_ip: tuple[int, float] | None = (10, 60.0),
    per_body_key: tuple[str, int, float] | None = None,
) -> Callable[[Request], Coroutine[Any, Any, None]]:
    """FastAPI dependency factory: ``per_ip = (limit, window_sec)`` and optional ``(json_field, limit, window)``.

    The body key (e.g. the login e-mail) is read from the JSON body without consuming it for the handler.
    """
    ip_limiter = SlidingWindowLimiter(*per_ip) if per_ip else None
    body_limiter = SlidingWindowLimiter(per_body_key[1], per_body_key[2]) if per_body_key else None
    LIMITERS[scope] = [limiter for limiter in (ip_limiter, body_limiter) if limiter is not None]

    async def _dep(request: Request) -> None:
        if not rate_limiting_enabled():
            return
        if ip_limiter is not None:
            retry = ip_limiter.check(f"ip:{client_ip(request)}")
            if retry is not None:
                raise _too_many(retry, scope, "ip")
        if body_limiter is not None and per_body_key is not None:
            value = await _json_field(request, per_body_key[0])
            if value:
                retry = body_limiter.check(f"{per_body_key[0]}:{value}")
                if retry is not None:
                    raise _too_many(retry, scope, per_body_key[0])

    return _dep


async def _json_field(request: Request, field: str) -> str | None:
    try:
        body = await request.json()
    except Exception:  # not JSON / empty — the handler's validation reports it
        return None
    value = body.get(field) if isinstance(body, dict) else None
    return str(value).strip().lower()[:256] if isinstance(value, str) else None


# scope -> limiters, so tests can reset state between cases.
LIMITERS: dict[str, list[SlidingWindowLimiter]] = {}


def reset_all() -> None:
    """Clear every limiter (tests)."""
    for limiters in LIMITERS.values():
        for limiter in limiters:
            limiter.reset()
