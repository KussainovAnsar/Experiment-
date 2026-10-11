"""Google Cloud Speech-to-Text v2 via REST (httpx) with a service-account bearer token.

Authentication: ``GOOGLE_APPLICATION_CREDENTIALS`` must point at a service-account JSON key; the token is
minted with ``google-auth`` (optional dependency: ``pip install google-auth``). Alternatively set
``GOOGLE_STT_ACCESS_TOKEN`` to a pre-obtained OAuth2 token (useful for one-off benchmarks).

The synchronous ``recognize`` method accepts inline audio of at most ~60 s, so the lesson is cut into
55-second chunks and each is sent as base64 ``content``; timestamps are shifted back. ``batchRecognize``
(GCS URIs) is deliberately not used to avoid an Object Storage dependency.

Settings: ``GOOGLE_STT_LOCATION`` (default ``us-central1``), ``GOOGLE_STT_MODEL`` (default ``chirp_2``),
``GOOGLE_STT_RECOGNIZER`` (default ``_``). Diarization is read from word ``speakerLabel`` when the model
provides it; otherwise speakers are ``unknown``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path

import httpx

from app.logging import get_logger
from app.providers.stt.base import (
    ProviderNotConfigured,
    Segment,
    STTError,
    TranscriptResult,
    Word,
    bcp47,
    env_timeout,
    finalize,
    primary_language,
    probe_duration,
    segments_from_words,
    shift,
    split_audio,
)

log = get_logger("app.providers.stt.google")

CHUNK_SEC = 55
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def _parse_duration(value: object) -> float:
    """``"12.300s"`` → 12.3 (v2 returns protobuf Duration strings)."""
    if value is None:
        return 0.0
    s = str(value).rstrip("s")
    try:
        return float(s)
    except ValueError:
        return 0.0


class GoogleSTTProvider:
    """Speech-to-Text v2 ``recognize`` adapter."""

    name = "google"

    def __init__(self, credentials_path: str, *, ffmpeg_bin: str = "ffmpeg") -> None:
        self.credentials_path = credentials_path
        self.static_token = os.environ.get("GOOGLE_STT_ACCESS_TOKEN", "")
        if not credentials_path and not self.static_token:
            raise ProviderNotConfigured(
                "STT_PROVIDER=google requires GOOGLE_APPLICATION_CREDENTIALS (service-account JSON path) "
                "or GOOGLE_STT_ACCESS_TOKEN"
            )
        self.location = os.environ.get("GOOGLE_STT_LOCATION", "us-central1")
        self.model = os.environ.get("GOOGLE_STT_MODEL", "chirp_2")
        self.recognizer = os.environ.get("GOOGLE_STT_RECOGNIZER", "_")
        self.ffmpeg_bin = ffmpeg_bin
        self.timeout = env_timeout("STT_HTTP_TIMEOUT_SEC", 300.0)
        self._project_id: str | None = os.environ.get("GOOGLE_CLOUD_PROJECT") or None

    # ------------------------------------------------------------ auth

    def _token_sync(self) -> tuple[str, str]:
        """Return ``(access_token, project_id)``."""
        if self.static_token:
            if not self._project_id:
                raise ProviderNotConfigured("GOOGLE_STT_ACCESS_TOKEN also needs GOOGLE_CLOUD_PROJECT")
            return self.static_token, self._project_id
        path = Path(self.credentials_path)
        if not path.is_file():
            raise ProviderNotConfigured(f"GOOGLE_APPLICATION_CREDENTIALS file not found: {path}")
        try:
            from google.auth.transport.requests import Request
            from google.oauth2 import service_account
        except ImportError as exc:
            raise ProviderNotConfigured(
                "STT_PROVIDER=google needs the optional package google-auth: pip install google-auth "
                "(or set GOOGLE_STT_ACCESS_TOKEN + GOOGLE_CLOUD_PROJECT)"
            ) from exc
        creds = service_account.Credentials.from_service_account_file(str(path), scopes=SCOPES)
        creds.refresh(Request())
        if not self._project_id:
            with path.open(encoding="utf-8") as fh:
                self._project_id = json.load(fh).get("project_id")
        if not self._project_id:
            raise ProviderNotConfigured("Could not determine Google project id; set GOOGLE_CLOUD_PROJECT")
        return str(creds.token), self._project_id

    # ------------------------------------------------------------ request

    def _endpoint(self, project_id: str) -> str:
        host = "speech.googleapis.com" if self.location == "global" else f"{self.location}-speech.googleapis.com"
        return (
            f"https://{host}/v2/projects/{project_id}/locations/{self.location}/recognizers/{self.recognizer}:recognize"
        )

    def _config(self, language_hints: list[str]) -> dict:
        codes = [bcp47(h) for h in language_hints if h]
        if self.model.startswith("chirp") and len(codes) != 1:
            language_codes = ["auto"]
        else:
            language_codes = codes or ["ru-RU"]
        return {
            "autoDecodingConfig": {},
            "model": self.model,
            "languageCodes": language_codes,
            "features": {
                "enableWordTimeOffsets": True,
                "enableWordConfidence": True,
                "enableAutomaticPunctuation": True,
            },
        }

    async def transcribe(self, audio_path: Path, *, language_hints: list[str]) -> TranscriptResult:
        """Chunk to ≤ 55 s pieces and call ``recognize`` for each."""
        audio_path = Path(audio_path)
        if not audio_path.is_file():
            raise STTError(f"audio file not found: {audio_path}")
        token, project_id = await asyncio.to_thread(self._token_sync)
        duration = probe_duration(audio_path)
        chunks = split_audio(audio_path, chunk_sec=CHUNK_SEC, duration_sec=duration, ffmpeg_bin=self.ffmpeg_bin)
        url = self._endpoint(project_id)
        config = self._config(language_hints)
        all_segments: list[Segment] = []
        all_labels: list[str | None] = []
        detected: str | None = None
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                for offset, path in chunks:
                    content = base64.b64encode(path.read_bytes()).decode("ascii")
                    resp = await client.post(
                        url,
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                        json={"config": config, "content": content},
                    )
                    if resp.status_code >= 400:
                        raise STTError(f"Google STT HTTP {resp.status_code}: {resp.text[:500]}")
                    segs, labs, lang = _parse_results(resp.json())
                    all_segments += shift(segs, offset)
                    all_labels += labs
                    detected = detected or lang
        except httpx.HTTPError as exc:
            raise STTError(f"Google STT request failed: {exc}") from exc
        finally:
            for _, p in chunks:
                p.unlink(missing_ok=True)
        language = (detected or "").split("-")[0].lower() or primary_language(language_hints)
        log.info("google_stt_done", model=self.model, chunks=len(chunks), segments=len(all_segments))
        return finalize(self.name, language, all_segments, all_labels)


def _parse_results(body: dict) -> tuple[list[Segment], list[str | None], str | None]:
    words: list[Word] = []
    detected: str | None = None
    fallback_text: list[str] = []
    for result in body.get("results") or []:
        alts = result.get("alternatives") or []
        if not alts:
            continue
        alt = alts[0]
        detected = detected or result.get("languageCode")
        if alt.get("words"):
            for w in alt["words"]:
                words.append(
                    Word(
                        text=w.get("word") or "",
                        start=_parse_duration(w.get("startOffset")),
                        end=_parse_duration(w.get("endOffset")),
                        speaker=w.get("speakerLabel"),
                        confidence=float(w["confidence"]) if w.get("confidence") is not None else None,
                    )
                )
        elif (alt.get("transcript") or "").strip():
            fallback_text.append(alt["transcript"].strip())
    segments, labels = segments_from_words(words)
    if not segments and fallback_text:
        from app.providers.stt.base import SPEAKER_UNKNOWN

        segments = [Segment(start=0.0, end=0.0, speaker=SPEAKER_UNKNOWN, text=" ".join(fallback_text))]
        labels = [None]
    return segments, labels, detected
