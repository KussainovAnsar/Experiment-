"""Local Whisper via ``faster-whisper`` (CTranslate2) — no API key, runs on CPU.

``faster-whisper`` is an OPTIONAL dependency and is intentionally NOT listed in ``requirements.txt``
(it pulls CTranslate2 + model downloads of 0.5–3 GB). Install it explicitly when you want offline STT::

    pip install faster-whisper

Model name comes from ``WHISPER_MODEL`` (tiny|base|small|medium|large-v3|large-v3-turbo); the first run
downloads the model to the Hugging Face cache. Whisper has no diarization, so every segment is
``unknown`` and ``speaker_map`` is empty — the analysis prompt must cope with that honestly.
Language is auto-detected (``language=None``) because lessons mix Russian and Kazakh.

Speed knobs (env), measured on 4 vCPU in benchmarks/ — large-v3-turbo greedy ≈ 0.78x real time on CPU,
i.e. ~35 min for a 45-minute lesson; on a GPU the same lesson takes ~1–2 min:

* ``WHISPER_DEVICE`` — ``auto`` (CUDA when available, else CPU), ``cpu`` or ``cuda``;
* ``WHISPER_COMPUTE_TYPE`` — default ``int8`` on CPU, ``float16`` on CUDA;
* ``WHISPER_BEAM_SIZE`` — default 1 (greedy): ~2x faster than beam 5, the benchmark numbers are greedy;
* ``WHISPER_BATCH_SIZE`` — >0 uses ``BatchedInferencePipeline`` (VAD chunks decoded in parallel batches);
  default 16 on CUDA, 0 (sequential) on CPU;
* ``WHISPER_CPU_THREADS`` — default: all cores.

Silence is skipped by the VAD filter, so pauses in the lesson cost nothing.
"""

from __future__ import annotations

import asyncio
import os
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

_MODEL_CACHE: dict[tuple[str, str, str, int], Any] = {}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def resolve_device(requested: str | None = None) -> str:
    """``auto`` → ``cuda`` when CTranslate2 sees a GPU, else ``cpu``."""
    device = (requested or os.environ.get("WHISPER_DEVICE") or "auto").strip().lower()
    if device != "auto":
        return device
    try:
        import ctranslate2

        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:  # pragma: no cover - ctranslate2 missing or broken CUDA setup
        return "cpu"


def _load_model(model_name: str, device: str, compute_type: str, cpu_threads: int) -> Any:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ProviderNotConfigured(
            "STT_PROVIDER=whisper_local requires the optional package faster-whisper: pip install faster-whisper"
        ) from exc
    key = (model_name, device, compute_type, cpu_threads)
    model = _MODEL_CACHE.get(key)
    if model is None:
        log.info("whisper_model_loading", model=model_name, device=device, compute_type=compute_type)
        model = WhisperModel(model_name, device=device, compute_type=compute_type, cpu_threads=cpu_threads)
        _MODEL_CACHE[key] = model
    return model


class WhisperLocalProvider:
    """faster-whisper adapter (CPU int8 or CUDA float16; optional batched inference)."""

    name = "whisper_local"
    prefers_compact_audio = False  # decodes the local 16 kHz WAV directly

    def __init__(
        self,
        model_name: str = "small",
        *,
        device: str | None = None,
        compute_type: str | None = None,
        beam_size: int | None = None,
        batch_size: int | None = None,
        cpu_threads: int | None = None,
    ) -> None:
        self.model_name = model_name or "small"
        self.device = resolve_device(device)
        gpu = self.device == "cuda"
        self.compute_type = compute_type or os.environ.get("WHISPER_COMPUTE_TYPE") or ("float16" if gpu else "int8")
        self.beam_size = max(1, beam_size if beam_size is not None else _env_int("WHISPER_BEAM_SIZE", 1))
        default_batch = 16 if gpu else 0
        batch = batch_size if batch_size is not None else _env_int("WHISPER_BATCH_SIZE", default_batch)
        self.batch_size = max(0, batch)
        default_threads = os.cpu_count() or 4
        self.cpu_threads = cpu_threads if cpu_threads is not None else _env_int("WHISPER_CPU_THREADS", default_threads)

    def _transcribe_sync(self, audio_path: Path, language_hints: list[str]) -> TranscriptResult:
        model = _load_model(self.model_name, self.device, self.compute_type, self.cpu_threads)
        kwargs: dict[str, Any] = {
            "language": None,  # auto-detect: lessons mix ru/kk
            "beam_size": self.beam_size,
            "vad_filter": True,  # pauses cost nothing
            "condition_on_previous_text": False,
        }
        try:
            if self.batch_size > 0:
                from faster_whisper import BatchedInferencePipeline

                raw_segments, info = BatchedInferencePipeline(model=model).transcribe(
                    str(audio_path), batch_size=self.batch_size, **kwargs
                )
            else:
                raw_segments, info = model.transcribe(str(audio_path), **kwargs)
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
            device=self.device,
            beam_size=self.beam_size,
            batch_size=self.batch_size,
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
