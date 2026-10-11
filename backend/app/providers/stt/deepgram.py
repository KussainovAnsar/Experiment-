"""Deepgram pre-recorded transcription: ``POST https://api.deepgram.com/v1/listen``.

Diarization (``diarize=true``), ``smart_format``, ``utterances=true`` so we receive speaker-attributed
utterances with timings. Model via ``DEEPGRAM_MODEL`` (default ``nova-3``). With one language hint we pass
``language=<hint>``; with several we enable ``detect_language``. Kazakh is not a Deepgram language as of
2026 — expect Russian-only quality on mixed lessons (see docs/PROVIDERS.md).
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from app.logging import get_logger
from app.providers.stt.base import (
    SPEAKER_UNKNOWN,
    ProviderNotConfigured,
    Segment,
    STTError,
    TranscriptResult,
    Word,
    aiter_file,
    audio_content_type,
    env_timeout,
    finalize,
    primary_language,
    segments_from_words,
)

log = get_logger("app.providers.stt.deepgram")

API_URL = "https://api.deepgram.com/v1/listen"


class DeepgramProvider:
    """Deepgram REST adapter."""

    name = "deepgram"
    prefers_compact_audio = True  # Ogg/Opus upload: ~8x smaller than the WAV

    def __init__(self, api_key: str, *, model: str | None = None) -> None:
        if not api_key:
            raise ProviderNotConfigured("STT_PROVIDER=deepgram requires DEEPGRAM_API_KEY")
        self.api_key = api_key
        self.model = model or os.environ.get("DEEPGRAM_MODEL", "nova-3")
        self.timeout = env_timeout("STT_HTTP_TIMEOUT_SEC", 600.0)

    def _params(self, language_hints: list[str]) -> dict[str, str]:
        params = {
            "model": self.model,
            "diarize": "true",
            "smart_format": "true",
            "punctuate": "true",
            "utterances": "true",
        }
        hints = [h for h in language_hints if h]
        if len(hints) == 1:
            params["language"] = primary_language(hints) or "ru"
        elif hints:
            params["detect_language"] = "true"
        return params

    async def transcribe(self, audio_path: Path, *, language_hints: list[str]) -> TranscriptResult:
        """Upload the audio (compact Ogg/Opus when the pipeline has it) and map utterances to segments."""
        audio_path = Path(audio_path)
        if not audio_path.is_file():
            raise STTError(f"audio file not found: {audio_path}")
        headers = {"Authorization": f"Token {self.api_key}", "Content-Type": audio_content_type(audio_path)}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    API_URL, params=self._params(language_hints), headers=headers, content=aiter_file(audio_path)
                )
        except httpx.HTTPError as exc:
            raise STTError(f"Deepgram request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise STTError(f"Deepgram HTTP {resp.status_code}: {resp.text[:500]}")
        body = resp.json()
        results = body.get("results") or {}
        channels = results.get("channels") or [{}]
        detected = channels[0].get("detected_language") if channels else None

        segments: list[Segment] = []
        labels: list[str | None] = []
        for utt in results.get("utterances") or []:
            text = (utt.get("transcript") or "").strip()
            if not text:
                continue
            segments.append(
                Segment(
                    start=float(utt.get("start", 0.0)),
                    end=float(utt.get("end", utt.get("start", 0.0))),
                    speaker=SPEAKER_UNKNOWN,
                    text=text,
                    confidence=float(utt["confidence"]) if utt.get("confidence") is not None else None,
                )
            )
            spk = utt.get("speaker")
            labels.append(f"SPEAKER_{int(spk):02d}" if spk is not None else None)
        if not segments:
            # Fall back to word timings of the first channel alternative.
            alt = ((channels[0].get("alternatives") or [{}])[0]) if channels else {}
            words = [
                Word(
                    text=w.get("punctuated_word") or w.get("word") or "",
                    start=float(w.get("start", 0.0)),
                    end=float(w.get("end", 0.0)),
                    speaker=f"SPEAKER_{int(w['speaker']):02d}" if w.get("speaker") is not None else None,
                    confidence=float(w["confidence"]) if w.get("confidence") is not None else None,
                )
                for w in alt.get("words") or []
            ]
            segments, labels = segments_from_words(words)
        log.info("deepgram_done", model=self.model, segments=len(segments), language=detected)
        return finalize(self.name, detected or primary_language(language_hints), segments, labels)
