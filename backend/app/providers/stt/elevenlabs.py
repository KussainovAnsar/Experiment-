"""ElevenLabs Scribe: ``POST https://api.elevenlabs.io/v1/speech-to-text`` (multipart) with ``diarize=true``.

Scribe returns word-level timings with ``speaker_id``; words are regrouped into segments with
:func:`segments_from_words`. ``language_code`` is passed only with a single hint (Scribe auto-detects
otherwise; it supports both Russian and Kazakh). Model via ``ELEVENLABS_STT_MODEL`` (default ``scribe_v1``).
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from app.logging import get_logger
from app.providers.stt.base import (
    ProviderNotConfigured,
    STTError,
    TranscriptResult,
    Word,
    env_timeout,
    finalize,
    primary_language,
    segments_from_words,
)

log = get_logger("app.providers.stt.elevenlabs")

API_URL = "https://api.elevenlabs.io/v1/speech-to-text"


class ElevenLabsProvider:
    """ElevenLabs Scribe adapter."""

    name = "elevenlabs"

    def __init__(self, api_key: str, *, model_id: str | None = None) -> None:
        if not api_key:
            raise ProviderNotConfigured("STT_PROVIDER=elevenlabs requires ELEVENLABS_API_KEY")
        self.api_key = api_key
        self.model_id = model_id or os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v1")
        self.timeout = env_timeout("STT_HTTP_TIMEOUT_SEC", 900.0)

    async def transcribe(self, audio_path: Path, *, language_hints: list[str]) -> TranscriptResult:
        """Upload the wav; regroup diarized words into segments."""
        audio_path = Path(audio_path)
        if not audio_path.is_file():
            raise STTError(f"audio file not found: {audio_path}")
        hints = [h for h in language_hints if h]
        data: dict[str, str] = {
            "model_id": self.model_id,
            "diarize": "true",
            "timestamps_granularity": "word",
            "tag_audio_events": "false",
        }
        if len(hints) == 1:
            data["language_code"] = primary_language(hints) or "ru"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                with audio_path.open("rb") as fh:
                    resp = await client.post(
                        API_URL,
                        headers={"xi-api-key": self.api_key},
                        data=data,
                        files={"file": (audio_path.name, fh, "audio/wav")},
                    )
        except httpx.HTTPError as exc:
            raise STTError(f"ElevenLabs request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise STTError(f"ElevenLabs HTTP {resp.status_code}: {resp.text[:500]}")
        body = resp.json()
        words = [
            Word(
                text=w.get("text") or "",
                start=float(w.get("start", 0.0)),
                end=float(w.get("end", w.get("start", 0.0))),
                speaker=w.get("speaker_id"),
                confidence=None,  # Scribe exposes no per-word confidence
            )
            for w in body.get("words") or []
            if w.get("type", "word") == "word"
        ]
        segments, labels = segments_from_words(words)
        if not segments and (body.get("text") or "").strip():
            from app.providers.stt.base import SPEAKER_UNKNOWN, Segment

            segments = [Segment(start=0.0, end=0.0, speaker=SPEAKER_UNKNOWN, text=body["text"].strip())]
            labels = [None]
        language = body.get("language_code") or primary_language(language_hints)
        log.info("elevenlabs_done", model=self.model_id, segments=len(segments), language=language)
        return finalize(self.name, language, segments, labels)
