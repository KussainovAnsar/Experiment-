"""Cloud STT uploads: compact Ogg/Opus audio is streamed through httpx.AsyncClient (no sync file objects)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest

from app.pipeline.media import extract_audio, extract_compact_audio, probe_media
from app.providers.stt import assemblyai, deepgram
from app.providers.stt.base import audio_content_type


@pytest.fixture()
def lesson_media(tmp_path: Path) -> Path:
    src = tmp_path / "lesson.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x240:r=10:d=6",
         "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=44100:d=6", "-shortest",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src)],
        check=True,
        capture_output=True,
    )  # fmt: skip
    return src


def test_compact_audio_copies_aac_without_reencoding(lesson_media: Path, tmp_path: Path) -> None:
    wav = extract_audio(lesson_media, tmp_path / "audio.wav")
    info = probe_media(lesson_media)
    compact = extract_compact_audio(lesson_media, tmp_path, audio_codec=info.audio_codec)
    assert compact.name == "audio.m4a" and probe_media(compact).audio_codec == "aac"
    assert abs(probe_media(compact).duration_sec - 6.0) < 0.3
    assert compact.stat().st_size * 3 < wav.stat().st_size
    assert audio_content_type(compact) == "audio/mp4" and audio_content_type(wav) == "audio/wav"


def test_compact_audio_reencodes_other_codecs_to_opus(lesson_media: Path, tmp_path: Path) -> None:
    wav = extract_audio(lesson_media, tmp_path / "src" / "audio.wav")
    old = tmp_path / "audio.m4a"
    old.write_bytes(b"stale")  # left over from a previous run with an AAC source
    compact = extract_compact_audio(wav, tmp_path, audio_codec="pcm_s16le")
    assert compact.name == "audio.ogg" and probe_media(compact).audio_codec == "opus"
    assert not old.exists()
    assert audio_content_type(compact) == "audio/ogg"


def _patch_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


async def test_deepgram_streams_compact_audio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audio = tmp_path / "audio.ogg"
    audio.write_bytes(b"OggS" + b"\0" * 5000)
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["type"] = request.headers["content-type"]
        seen["size"] = len(await request.aread())
        utt = {"start": 0.0, "end": 1.0, "transcript": "Сәлем", "confidence": 0.9, "speaker": 0}
        return httpx.Response(200, json={"results": {"channels": [{}], "utterances": [utt]}})

    _patch_client(monkeypatch, handler)
    provider = deepgram.DeepgramProvider("key")
    assert provider.prefers_compact_audio is True
    result = await provider.transcribe(audio, language_hints=["ru", "kk"])
    assert seen == {"type": "audio/ogg", "size": 5004}
    assert result.segments[0].text == "Сәлем"


async def test_assemblyai_upload_is_streamed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audio = tmp_path / "audio.ogg"
    audio.write_bytes(b"OggS" + b"\0" * 3000)
    uploaded: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/upload"):
            uploaded.append(len(await request.aread()))
            return httpx.Response(200, json={"upload_url": "https://cdn/x"})
        if request.method == "POST":
            return httpx.Response(200, json={"id": "t1"})
        return httpx.Response(200, json={"status": "completed", "language_code": "ru", "utterances": []})

    _patch_client(monkeypatch, handler)
    await assemblyai.AssemblyAIProvider("key").transcribe(audio, language_hints=["ru"])
    assert uploaded == [3004]
