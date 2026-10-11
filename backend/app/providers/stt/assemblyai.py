"""AssemblyAI: upload (``POST /v2/upload``) → create transcript (``speaker_labels=true``) → poll until done.

``language_detection=true`` when several hints are given, else ``language_code=<hint>``. Utterances carry
speaker letters (A, B, …) that :func:`assign_speaker_roles` turns into teacher/student. Poll interval and
overall timeout: ``STT_POLL_INTERVAL_SEC`` (3 s), ``STT_POLL_TIMEOUT_SEC`` (1 h).
"""

from __future__ import annotations

import asyncio
import os
import time
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
    env_timeout,
    finalize,
    primary_language,
    segments_from_words,
)

log = get_logger("app.providers.stt.assemblyai")

BASE_URL = "https://api.assemblyai.com/v2"


class AssemblyAIProvider:
    """AssemblyAI REST adapter."""

    name = "assemblyai"
    prefers_compact_audio = True  # Ogg/Opus upload: ~8x smaller than the WAV

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ProviderNotConfigured("STT_PROVIDER=assemblyai requires ASSEMBLYAI_API_KEY")
        self.api_key = api_key
        self.speech_model = os.environ.get("ASSEMBLYAI_SPEECH_MODEL", "")  # e.g. "universal", "slam-1"
        self.timeout = env_timeout("STT_HTTP_TIMEOUT_SEC", 600.0)
        self.poll_interval = env_timeout("STT_POLL_INTERVAL_SEC", 3.0)
        self.poll_timeout = env_timeout("STT_POLL_TIMEOUT_SEC", 3600.0)

    async def transcribe(self, audio_path: Path, *, language_hints: list[str]) -> TranscriptResult:
        """Upload, submit, poll, map."""
        audio_path = Path(audio_path)
        if not audio_path.is_file():
            raise STTError(f"audio file not found: {audio_path}")
        headers = {"authorization": self.api_key}
        hints = [h for h in language_hints if h]
        payload: dict[str, object] = {"speaker_labels": True, "punctuate": True, "format_text": True}
        if len(hints) == 1:
            payload["language_code"] = primary_language(hints)
        else:
            payload["language_detection"] = True
        if self.speech_model:
            payload["speech_model"] = self.speech_model

        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
                up = await client.post(f"{BASE_URL}/upload", content=aiter_file(audio_path))
                if up.status_code >= 400:
                    raise STTError(f"AssemblyAI upload HTTP {up.status_code}: {up.text[:500]}")
                payload["audio_url"] = up.json()["upload_url"]
                created = await client.post(f"{BASE_URL}/transcript", json=payload)
                if created.status_code >= 400:
                    raise STTError(f"AssemblyAI create HTTP {created.status_code}: {created.text[:500]}")
                tid = created.json()["id"]
                body = await self._poll(client, tid)
        except httpx.HTTPError as exc:
            raise STTError(f"AssemblyAI request failed: {exc}") from exc

        segments: list[Segment] = []
        labels: list[str | None] = []
        for utt in body.get("utterances") or []:
            text = (utt.get("text") or "").strip()
            if not text:
                continue
            segments.append(
                Segment(
                    start=float(utt.get("start", 0)) / 1000.0,
                    end=float(utt.get("end", utt.get("start", 0))) / 1000.0,
                    speaker=SPEAKER_UNKNOWN,
                    text=text,
                    confidence=float(utt["confidence"]) if utt.get("confidence") is not None else None,
                )
            )
            labels.append(utt.get("speaker"))
        if not segments:
            words = [
                Word(
                    text=w.get("text") or "",
                    start=float(w.get("start", 0)) / 1000.0,
                    end=float(w.get("end", 0)) / 1000.0,
                    speaker=w.get("speaker"),
                    confidence=float(w["confidence"]) if w.get("confidence") is not None else None,
                )
                for w in body.get("words") or []
            ]
            segments, labels = segments_from_words(words)
        language = body.get("language_code") or primary_language(language_hints)
        log.info("assemblyai_done", transcript_id=body.get("id"), segments=len(segments), language=language)
        return finalize(self.name, language, segments, labels)

    async def _poll(self, client: httpx.AsyncClient, transcript_id: str) -> dict:
        deadline = time.monotonic() + self.poll_timeout
        while True:
            resp = await client.get(f"{BASE_URL}/transcript/{transcript_id}")
            if resp.status_code >= 400:
                raise STTError(f"AssemblyAI poll HTTP {resp.status_code}: {resp.text[:500]}")
            body = resp.json()
            status = body.get("status")
            if status == "completed":
                return body
            if status == "error":
                raise STTError(f"AssemblyAI transcription error: {body.get('error')}")
            if time.monotonic() > deadline:
                raise STTError(f"AssemblyAI transcription timed out after {self.poll_timeout:.0f}s")
            await asyncio.sleep(self.poll_interval)
