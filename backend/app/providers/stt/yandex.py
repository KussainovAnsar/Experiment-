"""Yandex SpeechKit (API v3, asynchronous file recognition) via REST.

Flow (works for recordings shorter than ~1 h and ≤ 1 GB without Object Storage):

1. ``POST https://stt.api.cloud.yandex.net/stt/v3/recognizeFileAsync`` with the WAV bytes base64-encoded in
   ``content`` (alternatively an Object Storage ``uri`` — set ``YANDEX_STT_URI_MODE=1`` and provide the uri
   yourself if your lessons exceed the inline limit);
2. poll ``GET https://operation.api.cloud.yandex.net/operations/{id}`` until ``done``;
3. ``GET https://stt.api.cloud.yandex.net/stt/v3/getRecognition?operation_id=...`` returns newline-delimited
   JSON events; we take ``finalRefinement`` (normalized text) when present, else ``final`` alternatives,
   with word timings in milliseconds.

Auth: ``Authorization: Api-Key <YANDEX_API_KEY>`` plus ``x-folder-id: <YANDEX_FOLDER_ID>``.
Languages: ``ru-RU`` and ``kk-KZ`` are both supported; with several hints we restrict to that whitelist.
SpeechKit v3 has no speaker diarization for mono files → speakers are ``unknown``.
"""

from __future__ import annotations

import asyncio
import base64
import json
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
    bcp47,
    env_timeout,
    finalize,
    primary_language,
)

log = get_logger("app.providers.stt.yandex")

RECOGNIZE_URL = "https://stt.api.cloud.yandex.net/stt/v3/recognizeFileAsync"
OPERATION_URL = "https://operation.api.cloud.yandex.net/operations/{id}"
RESULT_URL = "https://stt.api.cloud.yandex.net/stt/v3/getRecognition"


class YandexSpeechKitProvider:
    """SpeechKit v3 async-file adapter."""

    name = "yandex"

    def __init__(self, api_key: str, folder_id: str) -> None:
        if not api_key:
            raise ProviderNotConfigured("STT_PROVIDER=yandex requires YANDEX_API_KEY")
        if not folder_id:
            raise ProviderNotConfigured("STT_PROVIDER=yandex requires YANDEX_FOLDER_ID")
        self.api_key = api_key
        self.folder_id = folder_id
        self.model = os.environ.get("YANDEX_STT_MODEL", "general")
        self.timeout = env_timeout("STT_HTTP_TIMEOUT_SEC", 600.0)
        self.poll_interval = env_timeout("STT_POLL_INTERVAL_SEC", 3.0)
        self.poll_timeout = env_timeout("STT_POLL_TIMEOUT_SEC", 3600.0)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Api-Key {self.api_key}", "x-folder-id": self.folder_id}

    def _request_body(self, audio_b64: str, language_hints: list[str]) -> dict:
        codes = [bcp47(h) for h in language_hints if h] or ["ru-RU"]
        return {
            "content": audio_b64,
            "recognitionModel": {
                "model": self.model,
                "audioFormat": {"containerAudio": {"containerAudioType": "WAV"}},
                "textNormalization": {
                    "textNormalization": "TEXT_NORMALIZATION_ENABLED",
                    "profanityFilter": False,
                    "literatureText": True,
                },
                "languageRestriction": {"restrictionType": "WHITELIST", "languageCode": codes},
                "audioProcessingType": "FULL_DATA",
            },
        }

    async def transcribe(self, audio_path: Path, *, language_hints: list[str]) -> TranscriptResult:
        """Submit, poll, fetch results."""
        audio_path = Path(audio_path)
        if not audio_path.is_file():
            raise STTError(f"audio file not found: {audio_path}")
        audio_b64 = base64.b64encode(audio_path.read_bytes()).decode("ascii")
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=self._headers()) as client:
                resp = await client.post(RECOGNIZE_URL, json=self._request_body(audio_b64, language_hints))
                if resp.status_code >= 400:
                    raise STTError(f"SpeechKit submit HTTP {resp.status_code}: {resp.text[:500]}")
                op_id = resp.json().get("id")
                if not op_id:
                    raise STTError(f"SpeechKit returned no operation id: {resp.text[:300]}")
                await self._wait(client, op_id)
                result = await client.get(RESULT_URL, params={"operation_id": op_id})
                if result.status_code >= 400:
                    raise STTError(f"SpeechKit result HTTP {result.status_code}: {result.text[:500]}")
        except httpx.HTTPError as exc:
            raise STTError(f"SpeechKit request failed: {exc}") from exc
        segments, labels = _parse_events(result.text)
        log.info("yandex_stt_done", operation_id=op_id, segments=len(segments))
        return finalize(self.name, primary_language(language_hints), segments, labels)

    async def _wait(self, client: httpx.AsyncClient, op_id: str) -> None:
        deadline = time.monotonic() + self.poll_timeout
        while True:
            resp = await client.get(OPERATION_URL.format(id=op_id))
            if resp.status_code >= 400:
                raise STTError(f"SpeechKit operation HTTP {resp.status_code}: {resp.text[:500]}")
            body = resp.json()
            if body.get("done"):
                if body.get("error"):
                    raise STTError(f"SpeechKit operation failed: {body['error']}")
                return
            if time.monotonic() > deadline:
                raise STTError(f"SpeechKit operation timed out after {self.poll_timeout:.0f}s")
            await asyncio.sleep(self.poll_interval)


def _parse_events(ndjson: str) -> tuple[list[Segment], list[str | None]]:
    """Parse ``getRecognition`` output (one JSON object per line, possibly wrapped in ``{"result": ...}``)."""
    refined: list[Segment] = []
    finals: list[Segment] = []
    for line in ndjson.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = event.get("result", event)
        if "finalRefinement" in event:
            alts = ((event["finalRefinement"] or {}).get("normalizedText") or {}).get("alternatives") or []
            refined.extend(_segments_from_alternatives(alts))
        elif "final" in event:
            finals.extend(_segments_from_alternatives((event["final"] or {}).get("alternatives") or []))
    segments = refined or finals
    segments.sort(key=lambda s: s.start)
    return segments, [None] * len(segments)


def _segments_from_alternatives(alts: list[dict]) -> list[Segment]:
    if not alts:
        return []
    alt = alts[0]
    text = (alt.get("text") or "").strip()
    words = [
        Word(
            text=w.get("text") or "",
            start=float(w.get("startTimeMs", 0)) / 1000.0,
            end=float(w.get("endTimeMs", 0)) / 1000.0,
        )
        for w in alt.get("words") or []
    ]
    if words:
        start = words[0].start
        end = max(words[-1].end, start)
        if not text:
            text = " ".join(w.text for w in words)
    else:
        start = float(alt.get("startTimeMs", 0)) / 1000.0
        end = float(alt.get("endTimeMs", 0)) / 1000.0
    if not text:
        return []
    conf = alt.get("confidence")
    return [Segment(start=start, end=end, speaker=SPEAKER_UNKNOWN, text=text, confidence=float(conf) if conf else None)]
