"""OpenAI speech-to-text through ``POST /v1/audio/transcriptions`` (plain httpx, no SDK).

Models (``OPENAI_STT_MODEL``):

* ``whisper-1`` (default) — ``response_format=verbose_json`` returns timed segments;
* ``gpt-4o-transcribe-diarize`` — ``response_format=diarized_json`` returns segments with speaker labels;
* ``gpt-4o-transcribe`` / ``gpt-4o-mini-transcribe`` — text only; we return one segment per upload chunk.

The endpoint caps uploads at 25 MB, so the 16 kHz WAV is cut into 10-minute mono MP3 chunks (≈ 3.6 MB
each) and timestamps are shifted back. Whisper gives no speakers → ``unknown``.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from app.logging import get_logger
from app.providers.revolver import best_retry_after
from app.providers.stt.base import (
    SPEAKER_UNKNOWN,
    ProviderNotConfigured,
    Segment,
    STTError,
    STTHTTPError,
    TranscriptResult,
    env_timeout,
    finalize,
    logprob_to_confidence,
    primary_language,
    probe_duration,
    shift,
    split_audio,
)

log = get_logger("app.providers.stt.openai")

API_URL = "https://api.openai.com/v1/audio/transcriptions"
CHUNK_SEC = 600


class OpenAISTTProvider:
    """OpenAI transcription adapter."""

    name = "openai"

    def __init__(
        self,
        api_key: str,
        *,
        model: str | None = None,
        ffmpeg_bin: str = "ffmpeg",
        api_url: str = API_URL,
        name: str | None = None,
        label: str = "OpenAI",
    ) -> None:
        """``api_url``/``name``/``label`` reuse the adapter for OpenAI-compatible endpoints (Groq Whisper)."""
        if not api_key:
            raise ProviderNotConfigured(f"STT_PROVIDER={name or self.name} requires an API key (OPENAI_API_KEY)")
        self.api_key = api_key
        self.api_url = api_url
        self.label = label
        if name:
            self.name = name
        self.model = model or os.environ.get("OPENAI_STT_MODEL", "whisper-1")
        self.ffmpeg_bin = ffmpeg_bin
        self.timeout = env_timeout("STT_HTTP_TIMEOUT_SEC", 600.0)

    def _response_format(self) -> str:
        if self.model == "whisper-1" or self.model.startswith("whisper-large"):
            return "verbose_json"
        if self.model.endswith("-diarize"):
            return "diarized_json"
        return "json"

    async def _transcribe_chunk(
        self, client: httpx.AsyncClient, path: Path, language: str | None, offset: float, chunk_dur: float
    ) -> tuple[list[Segment], list[str | None], str | None]:
        data: dict[str, str] = {"model": self.model, "response_format": self._response_format()}
        if language:
            data["language"] = language
        if self._response_format() == "diarized_json":
            data["chunking_strategy"] = "auto"
        with path.open("rb") as fh:
            resp = await client.post(
                self.api_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                data=data,
                files={"file": (path.name, fh, "audio/mpeg")},
            )
        if resp.status_code >= 400:
            raise STTHTTPError(
                f"{self.label} STT HTTP {resp.status_code}: {resp.text[:500]}",
                status_code=resp.status_code,
                retry_after=best_retry_after(resp.headers, resp.text),
            )
        body = resp.json()
        detected = body.get("language")
        segments: list[Segment] = []
        labels: list[str | None] = []
        raw_segments = body.get("segments") or []
        if raw_segments:
            for s in raw_segments:
                text = (s.get("text") or "").strip()
                if not text:
                    continue
                segments.append(
                    Segment(
                        start=float(s.get("start", 0.0)),
                        end=float(s.get("end", s.get("start", 0.0))),
                        speaker=SPEAKER_UNKNOWN,
                        text=text,
                        confidence=_confidence(s),
                    )
                )
                labels.append(s.get("speaker"))
        elif (body.get("text") or "").strip():
            segments.append(Segment(start=0.0, end=chunk_dur, speaker=SPEAKER_UNKNOWN, text=body["text"].strip()))
            labels.append(None)
        shift(segments, offset)
        return segments, labels, detected

    async def transcribe(self, audio_path: Path, *, language_hints: list[str]) -> TranscriptResult:
        """Chunk, upload, merge."""
        audio_path = Path(audio_path)
        if not audio_path.is_file():
            raise STTError(f"audio file not found: {audio_path}")
        language = primary_language(language_hints)
        # Whisper has no Kazakh-specific hint value problems, but passing a single wrong language hurts
        # code-switched lessons; with several hints we let the model auto-detect.
        if len([h for h in language_hints if h]) > 1:
            language = None
        duration = probe_duration(audio_path)
        chunks = split_audio(audio_path, chunk_sec=CHUNK_SEC, duration_sec=duration, ffmpeg_bin=self.ffmpeg_bin)
        all_segments: list[Segment] = []
        all_labels: list[str | None] = []
        detected: str | None = None
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                for offset, path in chunks:
                    chunk_dur = min(CHUNK_SEC, max(duration - offset, 0.0)) or CHUNK_SEC
                    segs, labs, lang = await self._transcribe_chunk(client, path, language, offset, chunk_dur)
                    all_segments += segs
                    all_labels += labs
                    detected = detected or lang
        except httpx.HTTPError as exc:
            raise STTHTTPError(f"{self.label} STT request failed: {exc}") from exc
        finally:
            for _, p in chunks:
                p.unlink(missing_ok=True)
        log.info(
            "openai_stt_done", provider=self.name, model=self.model, chunks=len(chunks), segments=len(all_segments)
        )
        return finalize(self.name, detected or language, all_segments, all_labels)


def _confidence(segment: dict) -> float | None:
    if "avg_logprob" in segment:
        return logprob_to_confidence(segment.get("avg_logprob"))
    return None
