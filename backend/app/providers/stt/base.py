"""STT provider protocol, result types and shared helpers.

Every provider implements :class:`STTProvider` and returns a :class:`TranscriptResult`. Providers never
guess speakers by alternation: when the backend returns diarization labels they are mapped with
:func:`assign_speaker_roles` (most words → ``teacher``, the rest → ``student``); without labels every
segment is ``unknown`` and ``speaker_map`` is empty.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import subprocess
import tempfile
from collections import Counter
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.logging import get_logger

log = get_logger("app.providers.stt")

SPEAKER_TEACHER = "teacher"
SPEAKER_STUDENT = "student"
SPEAKER_UNKNOWN = "unknown"

# ISO-639-1 → BCP-47 tags most cloud STT APIs expect.
BCP47 = {"ru": "ru-RU", "kk": "kk-KZ", "en": "en-US"}
_SENTENCE_END_RE = re.compile(r"[.!?…]$")


class STTError(RuntimeError):
    """The provider failed (network, HTTP error, malformed response)."""


class ProviderNotConfigured(STTError):
    """The selected provider lacks credentials or an optional dependency; the message says how to fix it."""


@dataclass(slots=True)
class Segment:
    """One timed utterance of the transcript."""

    start: float
    end: float
    speaker: str  # teacher | student | unknown
    text: str
    confidence: float | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start"] = round(float(d["start"]), 3)
        d["end"] = round(float(d["end"]), 3)
        if d["confidence"] is not None:
            d["confidence"] = round(float(d["confidence"]), 4)
        return d


@dataclass(slots=True)
class TranscriptResult:
    """Provider-independent transcript."""

    provider: str
    language: str | None
    segments: list[Segment] = field(default_factory=list)
    speaker_map: dict[str, str] = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.text and s.text.strip())

    @property
    def word_count(self) -> int:
        return sum(len(s.text.split()) for s in self.segments)

    @property
    def duration_sec(self) -> float | None:
        return max((s.end for s in self.segments), default=None)

    def segments_as_dicts(self) -> list[dict]:
        return [s.to_dict() for s in self.segments]


@runtime_checkable
class STTProvider(Protocol):
    """Speech-to-text adapter. ``transcribe`` is a coroutine; CPU-bound work runs in a thread."""

    name: str

    async def transcribe(self, audio_path: Path, *, language_hints: list[str]) -> TranscriptResult: ...


# ---------------------------------------------------------------- shared helpers


def assign_speaker_roles(segments: Sequence[Segment], labels: Sequence[str | None]) -> dict[str, str]:
    """Map raw diarization labels to ``teacher``/``student`` in place and return the ``speaker_map``.

    ``labels[i]`` is the provider's raw speaker label of ``segments[i]`` (``None`` when absent). The label
    with the most words becomes the teacher; every other label becomes the student (one-to-one lesson).
    If no segment carries a label, all speakers are set to ``unknown`` and ``{}`` is returned.
    """
    if len(labels) != len(segments):
        raise ValueError("labels must be parallel to segments")
    words_by_label: Counter[str] = Counter()
    for seg, label in zip(segments, labels, strict=True):
        if label is not None and str(label) != "":
            words_by_label[str(label)] += max(1, len(seg.text.split()))
    if not words_by_label:
        for seg in segments:
            seg.speaker = SPEAKER_UNKNOWN
        return {}
    ranked = [label for label, _ in words_by_label.most_common()]
    speaker_map = {ranked[0]: SPEAKER_TEACHER}
    for other in ranked[1:]:
        speaker_map[other] = SPEAKER_STUDENT
    for seg, label in zip(segments, labels, strict=True):
        seg.speaker = speaker_map.get(str(label), SPEAKER_UNKNOWN) if label is not None else SPEAKER_UNKNOWN
    return speaker_map


@dataclass(slots=True)
class Word:
    """Word-level unit used to rebuild segments from word timings."""

    text: str
    start: float
    end: float
    speaker: str | None = None
    confidence: float | None = None


def segments_from_words(
    words: Iterable[Word], *, max_gap_sec: float = 1.2, max_segment_sec: float = 30.0
) -> tuple[list[Segment], list[str | None]]:
    """Group words into segments: a new segment starts on speaker change, a long pause, a sentence end
    (once the segment is ≥ 4 s) or when the segment exceeds ``max_segment_sec``.

    Returns the segments (speaker=``unknown``) and the parallel list of raw speaker labels for
    :func:`assign_speaker_roles`.
    """
    segments: list[Segment] = []
    labels: list[str | None] = []
    cur: list[Word] = []

    def flush() -> None:
        if not cur:
            return
        confs = [w.confidence for w in cur if w.confidence is not None]
        segments.append(
            Segment(
                start=cur[0].start,
                end=max(cur[-1].end, cur[0].start),
                speaker=SPEAKER_UNKNOWN,
                text=" ".join(w.text.strip() for w in cur if w.text.strip()),
                confidence=(sum(confs) / len(confs)) if confs else None,
            )
        )
        labels.append(cur[0].speaker)
        cur.clear()

    for w in words:
        if not w.text or not w.text.strip():
            continue
        if cur:
            prev = cur[-1]
            seg_len = w.end - cur[0].start
            if (
                (w.speaker != prev.speaker)
                or (w.start - prev.end > max_gap_sec)
                or (seg_len > max_segment_sec)
                or (_SENTENCE_END_RE.search(prev.text.strip()) and prev.end - cur[0].start >= 4.0)
            ):
                flush()
        cur.append(w)
    flush()
    return segments, labels


def finalize(
    provider: str, language: str | None, segments: list[Segment], labels: Sequence[str | None]
) -> TranscriptResult:
    """Assign speaker roles and build the result (drops empty segments, sorts by start)."""
    keep = [(s, lab) for s, lab in zip(segments, labels, strict=True) if s.text and s.text.strip()]
    keep.sort(key=lambda pair: pair[0].start)
    segs = [s for s, _ in keep]
    labs = [lab for _, lab in keep]
    speaker_map = assign_speaker_roles(segs, labs)
    return TranscriptResult(provider=provider, language=language, segments=segs, speaker_map=speaker_map)


def primary_language(language_hints: Sequence[str] | None) -> str | None:
    """First usable ISO-639-1 hint (lower-cased), or ``None``."""
    for h in language_hints or []:
        h = (h or "").strip().lower()
        if h:
            return h[:2]
    return None


def bcp47(code: str | None) -> str | None:
    """``ru`` → ``ru-RU``; passes through unknown codes unchanged."""
    if not code:
        return None
    return BCP47.get(code.lower(), code)


def logprob_to_confidence(avg_logprob: float | None) -> float | None:
    """Whisper ``avg_logprob`` → 0..1 confidence (``exp``), clipped."""
    if avg_logprob is None:
        return None
    try:
        return max(0.0, min(1.0, math.exp(avg_logprob)))
    except OverflowError:
        return None


def env_timeout(name: str, default: float) -> float:
    """Float timeout from the environment with a default."""
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


_AUDIO_CONTENT_TYPES = {
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
}


def audio_content_type(path: Path) -> str:
    """MIME type of an audio file we upload (by extension)."""
    return _AUDIO_CONTENT_TYPES.get(Path(path).suffix.lower(), "application/octet-stream")


async def aiter_file(path: Path, chunk_size: int = 1 << 20) -> AsyncIterator[bytes]:
    """Stream a file to ``httpx.AsyncClient(content=...)`` without loading it into memory.

    httpx refuses a plain (sync) file object as ``content`` of an async request, so uploads must go through
    an async iterator.
    """
    with Path(path).open("rb") as fh:
        while chunk := await asyncio.to_thread(fh.read, chunk_size):
            yield chunk


def transcode_for_upload(src: Path, *, bitrate: str = "32k", ffmpeg_bin: str = "ffmpeg") -> Path:
    """Transcode ``src`` to a mono MP3 at ``bitrate`` in a temp file (for APIs with upload size limits).

    A one-hour lesson at 32 kbit/s is ≈ 14 MB versus ≈ 115 MB of 16 kHz PCM. The caller removes the file.
    """
    fd, name = tempfile.mkstemp(prefix="llstt-", suffix=".mp3")
    os.close(fd)
    dst = Path(name)
    cmd = [ffmpeg_bin, "-y", "-nostdin", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-b:a", bitrate, str(dst)]
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", check=False)
    if proc.returncode != 0:
        dst.unlink(missing_ok=True)
        raise STTError(f"ffmpeg transcode failed: {proc.stderr.strip()[-500:]}")
    return dst


def split_audio(
    src: Path, *, chunk_sec: int, duration_sec: float, ffmpeg_bin: str = "ffmpeg"
) -> list[tuple[float, Path]]:
    """Cut ``src`` into consecutive mono MP3 chunks of ``chunk_sec``; returns ``[(offset_sec, path), ...]``.

    Used by providers that cap request size/duration (OpenAI 25 MB, Google sync ≤ 60 s). Caller removes files.
    """
    chunks: list[tuple[float, Path]] = []
    offset = 0.0
    total = max(duration_sec, 0.0)
    while offset < total or (offset == 0.0 and total == 0.0):
        fd, name = tempfile.mkstemp(prefix="llstt-chunk-", suffix=".mp3")
        os.close(fd)
        dst = Path(name)
        cmd = [
            ffmpeg_bin, "-y", "-nostdin", "-v", "error",
            "-ss", f"{offset:.3f}", "-t", str(chunk_sec), "-i", str(src),
            "-vn", "-ac", "1", "-b:a", "48k", str(dst),
        ]  # fmt: skip
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", check=False)
        if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
            for _, p in chunks:
                p.unlink(missing_ok=True)
            dst.unlink(missing_ok=True)
            raise STTError(f"ffmpeg split failed at {offset:.0f}s: {proc.stderr.strip()[-500:]}")
        chunks.append((offset, dst))
        offset += chunk_sec
        if total == 0.0:
            break
    return chunks


def probe_duration(path: Path, *, ffprobe_bin: str = "ffprobe") -> float:
    """Duration in seconds via ffprobe (0.0 when unknown)."""
    cmd = [ffprobe_bin, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", check=False)
    try:
        return max(0.0, float(proc.stdout.strip()))
    except ValueError:
        return 0.0


def shift(segments: list[Segment], offset_sec: float) -> list[Segment]:
    """Return segments shifted by ``offset_sec`` (for chunked transcription)."""
    if not offset_sec:
        return segments
    for s in segments:
        s.start += offset_sec
        s.end += offset_sec
    return segments
