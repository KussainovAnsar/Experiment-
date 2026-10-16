"""Free-tier STT "revolver" (``STT_PROVIDER=revolver``): Groq Whisper first, then optional credit-based APIs.

``REVOLVER_STT_ORDER`` (default :data:`DEFAULT_ORDER`) lists ``provider[:model]`` entries; an entry is enabled
only when its key is set. Groq is a permanent free plan; Deepgram / AssemblyAI / ElevenLabs only give a
one-off starter credit (or a small monthly allowance) — they are used only if their key is present.

Groq caps uploads at 25 MB on the free plan: :class:`~app.providers.stt.openai.OpenAISTTProvider` already cuts
the audio into 10-minute mono MP3 chunks (≈ 3.6 MB) and shifts the timestamps back, so whole lessons fit.
On 429 / 5xx / timeout / auth error / malformed answer the endpoint cools down (``Retry-After`` or
``REVOLVER_COOLDOWN_SEC``) and the next one transcribes the whole file again. When none is left →
:class:`FreeLimitsExhausted`; there is no paid fallback.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.config import Settings
from app.logging import get_logger
from app.providers.revolver import (
    STT_COOLDOWNS,
    CooldownBook,
    OrderEntry,
    cooldown_for,
    exhausted_message,
    parse_order,
)
from app.providers.stt.base import (
    FreeLimitsExhausted,
    ProviderNotConfigured,
    STTHTTPError,
    TranscriptResult,
)

log = get_logger("app.providers.stt.revolver")

GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
# whisper-large-v3 first: noticeably better than turbo on Kazakh / code-switched speech; each model has its own
# Groq quota, so turbo is a real second chance. Credit-based APIs follow and are enabled only by their key.
DEFAULT_ORDER = "groq:whisper-large-v3,groq:whisper-large-v3-turbo,deepgram,assemblyai"


def _groq(settings: Settings, model: str) -> Any:
    from app.providers.stt.openai import OpenAISTTProvider

    return OpenAISTTProvider(
        api_key=settings.groq_api_key,
        model=model or "whisper-large-v3",
        ffmpeg_bin=settings.ffmpeg_bin,
        api_url=GROQ_STT_URL,
        name="groq",
        label="Groq",
    )


def _deepgram(settings: Settings, model: str) -> Any:
    from app.providers.stt.deepgram import DeepgramProvider

    return DeepgramProvider(api_key=settings.deepgram_api_key, model=model or None)


def _assemblyai(settings: Settings, model: str) -> Any:
    from app.providers.stt.assemblyai import AssemblyAIProvider

    return AssemblyAIProvider(api_key=settings.assemblyai_api_key)


def _elevenlabs(settings: Settings, model: str) -> Any:
    from app.providers.stt.elevenlabs import ElevenLabsProvider

    return ElevenLabsProvider(api_key=settings.elevenlabs_api_key, model_id=model or None)


# name → (Settings key field, factory)
BACKENDS: dict[str, tuple[str, Callable[[Settings, str], Any]]] = {
    "groq": ("groq_api_key", _groq),
    "deepgram": ("deepgram_api_key", _deepgram),
    "assemblyai": ("assemblyai_api_key", _assemblyai),
    "elevenlabs": ("elevenlabs_api_key", _elevenlabs),
}


class RevolverSTTProvider:
    """Tries the configured STT endpoints in order with cooldowns (see module docstring)."""

    name = "revolver"

    def __init__(
        self,
        settings: Settings,
        *,
        cooldowns: CooldownBook | None = None,
        factories: dict[str, Callable[[Settings, str], Any]] | None = None,
    ) -> None:
        self.settings = settings
        self.cooldowns = cooldowns or STT_COOLDOWNS
        self.cooldown_sec = float(settings.revolver_cooldown_sec or 600.0)
        self._factories = factories or {k: f for k, (_, f) in BACKENDS.items()}
        self._providers: dict[str, Any] = {}
        self.entries: list[OrderEntry] = []
        missing: set[str] = set()
        for entry in parse_order(settings.revolver_stt_order or DEFAULT_ORDER):
            backend = BACKENDS.get(entry.provider)
            if backend is None:
                log.warning("stt_revolver_unknown_endpoint", endpoint=entry.provider)
                continue
            if not str(getattr(settings, backend[0], "") or "").strip():
                missing.add(backend[0].upper())
                continue
            self.entries.append(entry)
        if not self.entries:
            raise ProviderNotConfigured(
                "STT_PROVIDER=revolver: no endpoint has a key; set GROQ_API_KEY (free) "
                + (f"or one of {', '.join(sorted(missing - {'GROQ_API_KEY'}))}" if missing - {"GROQ_API_KEY"} else "")
            )
        log.info("stt_revolver_ready", endpoints=[e.key for e in self.entries])

    def _provider(self, entry: OrderEntry) -> Any:
        if entry.key not in self._providers:
            self._providers[entry.key] = self._factories[entry.provider](self.settings, entry.model)
        return self._providers[entry.key]

    async def transcribe(self, audio_path: Path, *, language_hints: list[str]) -> TranscriptResult:
        failures: list[str] = []
        for entry in self.entries:
            left = self.cooldowns.remaining(entry.key)
            if left > 0:
                failures.append(f"{entry.key}: остывает ещё {round(left)} с")
                continue
            try:
                result = await self._provider(entry).transcribe(audio_path, language_hints=language_hints)
            except Exception as exc:  # noqa: BLE001 - one endpoint failing means "try the next one"
                status = exc.status_code if isinstance(exc, STTHTTPError) else _status_from_text(str(exc))
                retry_after = exc.retry_after if isinstance(exc, STTHTTPError) else None
                cooldown = cooldown_for(status, retry_after, self.cooldown_sec)
                if cooldown is not None:
                    self.cooldowns.cool(entry.key, cooldown)
                log.warning(
                    "stt_revolver_endpoint_failed",
                    endpoint=entry.key,
                    status=status,
                    cooldown_sec=cooldown,
                    error=f"{type(exc).__name__}: {str(exc)[:300]}",
                )
                failures.append(f"{entry.key}: {f'HTTP {status}' if status else type(exc).__name__}")
                continue
            result.provider = entry.key[:64]  # transcripts.provider shows which free endpoint answered
            log.info("stt_revolver_answered", endpoint=entry.key, skipped=len(failures))
            return result
        waits = [w for w in (self.cooldowns.remaining(e.key) for e in self.entries) if w > 0]
        retry_after = min(waits) if waits else self.cooldown_sec
        log.error("stt_revolver_exhausted", failures=failures, retry_after_sec=retry_after)
        raise FreeLimitsExhausted(
            exhausted_message("распознавания речи", failures, retry_after), retry_after_sec=retry_after
        )


def _status_from_text(text: str) -> int | None:
    """Deepgram / AssemblyAI / ElevenLabs raise ``STTError("... HTTP 429: ...")``; recover the status."""
    m = re.search(r"HTTP (\d{3})", text)
    return int(m.group(1)) if m else None
