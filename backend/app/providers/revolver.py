"""Shared plumbing of the free-tier "revolver" providers (LLM and STT).

A revolver holds an ordered list of endpoints (``provider:model``), tries them one by one and puts a failed
endpoint on cooldown so the next lessons skip it until its quota is likely back. Cooldowns live in a
process-wide :class:`CooldownBook` (one per kind), so they survive between lessons handled by the same
API / worker process; they are not shared between processes (a fresh process simply re-tries and re-learns).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

DEFAULT_COOLDOWN_SEC = 600.0
MAX_COOLDOWN_SEC = 24 * 3600.0
# HTTP statuses that mean "the endpoint is broken/out of quota for everybody" (cooldown) as opposed to
# "this particular request is unsuitable" (400/413/422: skip to the next endpoint without a cooldown).
COOLDOWN_STATUSES = frozenset({401, 402, 403, 404, 408, 409, 429})
# 404 = the model was withdrawn ("no longer available"): it will not come back within the day.
GONE_STATUSES = frozenset({404})
# 503 = "the model is overloaded, try again later": a spike that usually passes within a minute or two.
OVERLOAD_STATUSES = frozenset({503})
OVERLOAD_COOLDOWN_SEC = 90.0


@dataclass(frozen=True, slots=True)
class OrderEntry:
    """One item of ``REVOLVER_*_ORDER``: ``provider:model[@max_images]``."""

    provider: str
    model: str
    max_images: int | None = None

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}" if self.model else self.provider


def parse_order(raw: str) -> list[OrderEntry]:
    """Parse ``"groq:whisper-large-v3,openrouter:google/gemma-4-31b-it:free@8,deepgram"``.

    The provider is everything before the first ``:`` (model ids may contain ``:``, e.g. OpenRouter ``:free``);
    an optional trailing ``@N`` caps the number of images sent to that endpoint (``@0`` = text only).
    Duplicates are dropped, order is kept.
    """
    out: list[OrderEntry] = []
    seen: set[str] = set()
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        max_images: int | None = None
        head, sep, tail = item.rpartition("@")
        if sep and tail.strip().isdigit():
            item, max_images = head.strip(), int(tail)
        provider, _, model = item.partition(":")
        entry = OrderEntry(provider.strip().lower(), model.strip(), max_images)
        if entry.provider and entry.key not in seen:
            seen.add(entry.key)
            out.append(entry)
    return out


def parse_retry_after(headers: Mapping[str, str] | None, *, now: datetime | None = None) -> float | None:
    """Seconds from a ``Retry-After`` header (delta-seconds or HTTP date); ``None`` if absent/unparseable."""
    if not headers:
        return None
    value = None
    for k, v in headers.items():
        if k.lower() == "retry-after":
            value = (v or "").strip()
            break
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


_HINT_RE = re.compile(
    r"(?:try again in|retry in|retryDelay[\"']?\s*:\s*[\"']?)\s*"
    r"(?:(?P<h>\d+(?:\.\d+)?)h)?(?:(?P<m>\d+(?:\.\d+)?)m(?!s))?(?:(?P<s>\d+(?:\.\d+)?)s)?",
    re.IGNORECASE,
)


def retry_hint_from_text(text: str | None) -> float | None:
    """Wait time hidden in an error body: Groq «Please try again in 7m12.5s», Gemini ``"retryDelay": "37s"``."""
    for m in _HINT_RE.finditer(text or ""):
        parts = [m.group("h"), m.group("m"), m.group("s")]
        if any(parts):
            h, mi, se = (float(x) if x else 0.0 for x in parts)
            return h * 3600 + mi * 60 + se
    return None


def best_retry_after(headers: Mapping[str, str] | None, text: str | None) -> float | None:
    """The longer of ``Retry-After`` and the hint in the body (servers often send ``Retry-After: 1`` for TPM)."""
    values = [v for v in (parse_retry_after(headers), retry_hint_from_text(text)) if v is not None]
    return max(values) if values else None


def cooldown_for(status_code: int | None, retry_after: float | None, default: float) -> float | None:
    """Cooldown in seconds for a failure, or ``None`` when the endpoint should just be skipped this time.

    ``status_code=None`` means timeout / connection / malformed output → cooldown. A withdrawn model (404) is
    cooled for :data:`MAX_COOLDOWN_SEC`; an overload (503) only briefly (:data:`OVERLOAD_COOLDOWN_SEC`).
    Otherwise Retry-After wins when sent.
    """
    if status_code in GONE_STATUSES:
        return MAX_COOLDOWN_SEC
    if status_code is not None and status_code < 500 and status_code not in COOLDOWN_STATUSES:
        return None
    if retry_after is not None and retry_after > 0:
        return min(max(retry_after, 1.0), MAX_COOLDOWN_SEC)
    if status_code in OVERLOAD_STATUSES:
        return min(default, OVERLOAD_COOLDOWN_SEC)
    return default


class CooldownBook:
    """``endpoint key → monotonic deadline``. ``clock`` is injectable for tests."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._until: dict[str, float] = {}

    def remaining(self, key: str) -> float:
        left = self._until.get(key, 0.0) - self.clock()
        if left <= 0:
            self._until.pop(key, None)
            return 0.0
        return left

    def cool(self, key: str, seconds: float) -> None:
        self._until[key] = max(self._until.get(key, 0.0), self.clock() + max(0.0, seconds))

    def reset(self) -> None:
        self._until.clear()


LLM_COOLDOWNS = CooldownBook()
STT_COOLDOWNS = CooldownBook()


def exhausted_message(kind: str, failures: list[str], retry_after: float | None) -> str:
    """Russian user-facing text: «бесплатные лимиты исчерпаны, повторите позже» + per-endpoint reasons."""
    when = f" (ближайший освободится примерно через {max(1, round(retry_after / 60))} мин)" if retry_after else ""
    details = "; ".join(failures) if failures else "нет ни одного настроенного эндпоинта"
    return f"Бесплатные лимиты {kind} исчерпаны, повторите позже{when}. Подробности: {details}"
