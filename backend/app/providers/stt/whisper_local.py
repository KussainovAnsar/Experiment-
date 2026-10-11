"""Local Whisper via ``faster-whisper`` (CTranslate2) — no API key, runs on CPU.

``faster-whisper`` is an OPTIONAL dependency and is intentionally NOT listed in ``requirements.txt``
(it pulls CTranslate2 + model downloads of 0.5–3 GB). Install it explicitly when you want offline STT::

    pip install faster-whisper

Model name comes from ``WHISPER_MODEL`` (tiny|base|small|medium|large-v3|large-v3-turbo); the first run
downloads the model to the Hugging Face cache. Whisper has no diarization, so every segment is
``unknown`` and ``speaker_map`` is empty — the analysis prompt must cope with that honestly.
Language is auto-detected (``language=None``) because lessons mix Russian and Kazakh.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.logging import get_logger
from app.providers.stt.base import (
    SPEAKER_UNKNOWN,
    ProviderNotConfigured,
    Segment,
    STTError,
    TranscriptResult,
    logprob_to_confidence,
)

log = get_logger("app.providers.stt.whisper_local")

_MODEL_CACHE: dict[tuple[str, str, str], Any] = {}


def _load_model(model_name: str, device: str, compute_type: str) -> Any:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ProviderNotConfigured(
            "STT_PROVIDER=whisper_local requires the optional package faster-whisper: pip install faster-whisper"
        ) from exc
    key = (model_name, device, compute_type)
    model = _MODEL_CACHE.get(key)
    if model is None:
        log.info("whisper_model_loading", model=model_name, device=device, compute_type=compute_type)
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
        _MODEL_CACHE[key] = model
    return model


class WhisperLocalProvider:
    """faster-whisper adapter (CPU, int8)."""

    name = "whisper_local"

    def __init__(self, model_name: str = "small", *, device: str = "cpu", compute_type: str = "int8") -> None:
        self.model_name = model_name or "small"
        self.device = device
        self.compute_type = compute_type

    def _transcribe_sync(self, audio_path: Path, language_hints: list[str]) -> TranscriptResult:
        model = _load_model(self.model_name, self.device, self.compute_type)
        try:
            raw_segments, info = model.transcribe(
                str(audio_path),
                language=None,  # auto-detect: lessons mix ru/kk
                beam_size=5,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            segments = [
                Segment(
                    start=float(s.start),
                    end=float(s.end),
                    speaker=SPEAKER_UNKNOWN,
                    text=(s.text or "").strip(),
                    confidence=logprob_to_confidence(getattr(s, "avg_logprob", None)),
                )
                for s in raw_segments
                if (s.text or "").strip()
            ]
        except Exception as exc:
            raise STTError(f"faster-whisper failed: {exc}") from exc
        language = getattr(info, "language", None)
        log.info(
            "whisper_done",
            model=self.model_name,
            language=language,
            language_probability=round(float(getattr(info, "language_probability", 0.0) or 0.0), 3),
            segments=len(segments),
        )
        return TranscriptResult(provider=self.name, language=language, segments=segments, speaker_map={})

    async def transcribe(self, audio_path: Path, *, language_hints: list[str]) -> TranscriptResult:
        """Run the model in a worker thread (CPU bound)."""
        audio_path = Path(audio_path)
        if not audio_path.is_file():
            raise STTError(f"audio file not found: {audio_path}")
        return await asyncio.to_thread(self._transcribe_sync, audio_path, list(language_hints))
