"""Media processing with ffmpeg/ffprobe and Pillow: probing, audio extraction, keyframe extraction.

All functions are synchronous and CPU/IO bound — the pipeline runner calls them through
``asyncio.to_thread`` so the event loop stays responsive in ``PIPELINE_MODE=inline``.

Keyframe strategy (docs/CONTRACT.md §1 step 4):

1. scene-change frames: ``select='gt(scene,THRESH)'`` with ``showinfo`` to recover exact ``pts_time``;
2. interval frames: one frame every ``interval_sec`` seconds;
3. merge + sort by time, compute a dHash (8x8 → 16 hex chars) per frame and drop near-duplicates
   (hamming distance ≤ 6 to the previously kept frame);
4. cap to ``max_frames`` preferring scene frames spread over the whole recording;
5. resize to max width 1280, save as ``NNNN.jpg`` (JPEG quality 85).

Audio-only inputs yield zero frames and no error. ffmpeg failures raise :class:`MediaError`
carrying the tail of stderr.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from PIL import Image

from app.config import get_settings
from app.logging import get_logger

log = get_logger("app.pipeline.media")

MAX_FRAME_WIDTH = 1280
JPEG_QUALITY = 85
DEDUP_HAMMING_THRESHOLD = 6
HASH_SIZE = 8
_STDERR_TAIL_CHARS = 2000
_SHOWINFO_RE = re.compile(r"\[Parsed_showinfo_(\d+) @ [^\]]+\].*?pts_time:\s*(-?[0-9.]+)")
_FRAME_FILE_RE = re.compile(r"^\d{4}\.jpg$")


def _ffmpeg_timeout() -> int:
    """Seconds a single ffmpeg/ffprobe call may run (``FFMPEG_TIMEOUT_SEC``, default 1 hour)."""
    try:
        return int(os.environ.get("FFMPEG_TIMEOUT_SEC", "3600"))
    except ValueError:
        return 3600


class MediaError(RuntimeError):
    """ffmpeg/ffprobe failed or produced unusable output. ``str(exc)`` contains the stderr tail."""


@dataclass(slots=True)
class MediaInfo:
    """Container/stream facts from ffprobe."""

    duration_sec: float
    has_video: bool
    has_audio: bool
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    format_name: str | None = None
    size_bytes: int | None = None


@dataclass(slots=True)
class ExtractedFrame:
    """One saved keyframe."""

    index: int
    timestamp_sec: float
    path: Path
    width: int
    height: int
    phash: str
    reason: str  # "scene" | "interval"


# ---------------------------------------------------------------- subprocess helpers


def _tail(text: str, limit: int = _STDERR_TAIL_CHARS) -> str:
    text = text.strip()
    return text if len(text) <= limit else "…" + text[-limit:]


def _run(cmd: list[str], *, what: str) -> subprocess.CompletedProcess[str]:
    """Run an ffmpeg-family command; raise :class:`MediaError` on non-zero exit, timeout or missing binary."""
    log.debug("media_cmd", what=what, argv=cmd[:12])
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_ffmpeg_timeout(),
            check=False,
        )
    except FileNotFoundError as exc:
        raise MediaError(f"{what}: binary not found: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"{what}: timed out after {_ffmpeg_timeout()}s") from exc
    if proc.returncode != 0:
        raise MediaError(f"{what}: exit code {proc.returncode}: {_tail(proc.stderr)}")
    return proc


def _ffmpeg_bin(override: str | None) -> str:
    return override or get_settings().ffmpeg_bin


def _ffprobe_bin(override: str | None) -> str:
    return override or get_settings().ffprobe_bin


# ---------------------------------------------------------------- probing


def _parse_fps(value: str | None) -> float | None:
    if not value or value in ("0/0", "0"):
        return None
    try:
        fps = float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return None
    return round(fps, 3) if fps > 0 else None


def _to_float(value: object) -> float | None:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f if f == f and f >= 0 else None  # reject NaN / negative


def probe_media(path: Path, *, ffprobe_bin: str | None = None) -> MediaInfo:
    """Return :class:`MediaInfo` for ``path`` using ``ffprobe -print_format json``.

    Streams flagged ``attached_pic`` (cover art in audio files) do not count as video.
    """
    path = Path(path)
    if not path.is_file():
        raise MediaError(f"probe: file not found: {path}")
    proc = _run(
        [
            _ffprobe_bin(ffprobe_bin),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        what="ffprobe",
    )
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise MediaError(f"ffprobe: invalid JSON output: {_tail(proc.stdout, 300)}") from exc

    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    video = next(
        (s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")),
        None,
    )
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None and audio is None:
        raise MediaError("ffprobe: no audio or video streams found")

    duration = _to_float(fmt.get("duration"))
    if duration is None:
        for s in (video, audio):
            if s is not None and (d := _to_float(s.get("duration"))) is not None:
                duration = d
                break
    if duration is None:
        duration = 0.0

    return MediaInfo(
        duration_sec=round(duration, 3),
        has_video=video is not None,
        has_audio=audio is not None,
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        fps=_parse_fps(video.get("avg_frame_rate") or video.get("r_frame_rate")) if video else None,
        video_codec=video.get("codec_name") if video else None,
        audio_codec=audio.get("codec_name") if audio else None,
        format_name=fmt.get("format_name"),
        size_bytes=int(fmt["size"]) if fmt.get("size") else None,
    )


# ---------------------------------------------------------------- audio


def extract_audio(src: Path, dst: Path, *, ffmpeg_bin: str | None = None) -> Path:
    """Extract/transcode the audio track of ``src`` into ``dst`` as 16 kHz mono PCM WAV."""
    src, dst = Path(src), Path(dst)
    if not src.is_file():
        raise MediaError(f"extract_audio: file not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".tmp.wav")
    _run(
        [
            _ffmpeg_bin(ffmpeg_bin),
            "-y",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(src),
            "-vn",
            "-sn",
            "-dn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            str(tmp),
        ],
        what="ffmpeg extract_audio",
    )
    if not tmp.exists() or tmp.stat().st_size <= 44:
        tmp.unlink(missing_ok=True)
        raise MediaError("ffmpeg extract_audio: produced an empty wav (no audio stream?)")
    tmp.replace(dst)
    return dst


# ---------------------------------------------------------------- hashing


def dhash(image: Image.Image, hash_size: int = HASH_SIZE) -> str:
    """Difference hash of an image: ``hash_size`` x ``hash_size`` bits as lower-case hex (16 chars for 8)."""
    gray = image.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    pixels = list(gray.getdata())
    bits = 0
    row_len = hash_size + 1
    for row in range(hash_size):
        base = row * row_len
        for col in range(hash_size):
            bits = (bits << 1) | (1 if pixels[base + col] > pixels[base + col + 1] else 0)
    return f"{bits:0{hash_size * hash_size // 4}x}"


def hamming_distance(a: str, b: str) -> int:
    """Number of differing bits between two hex hashes of equal length."""
    return bin(int(a, 16) ^ int(b, 16)).count("1")


# ---------------------------------------------------------------- keyframes


@dataclass(slots=True)
class _Candidate:
    timestamp_sec: float
    reason: str
    tmp_path: Path
    phash: str = ""


def _parse_showinfo(stderr: str) -> dict[int, list[float]]:
    """Group ``pts_time`` values by ``Parsed_showinfo_N`` filter instance (ordered by appearance in the graph)."""
    out: dict[int, list[float]] = {}
    for m in _SHOWINFO_RE.finditer(stderr):
        out.setdefault(int(m.group(1)), []).append(max(0.0, float(m.group(2))))
    return out


def _collect(tmp_dir: Path, prefix: str, timestamps: list[float], reason: str) -> list[_Candidate]:
    files = sorted(tmp_dir.glob(f"{prefix}_*.jpg"))
    n = min(len(files), len(timestamps))
    if len(files) != len(timestamps):
        log.warning("keyframes_count_mismatch", reason=reason, files=len(files), showinfo=len(timestamps))
    return [_Candidate(timestamp_sec=round(timestamps[i], 3), reason=reason, tmp_path=files[i]) for i in range(n)]


def _run_extraction(
    src: Path, tmp_dir: Path, *, interval_sec: int, scene_threshold: float, ffmpeg_bin: str | None
) -> list[_Candidate]:
    """Single decode pass producing scene and interval candidates (two filter branches)."""
    interval = max(1, int(interval_sec))
    scale = f"scale='min({MAX_FRAME_WIDTH},iw)':-2"
    graph = (
        "[0:v]split=2[sc_in][iv_in];"
        f"[sc_in]select='gt(scene,{scene_threshold:.4f})',{scale},showinfo[sc];"
        f"[iv_in]select='isnan(prev_selected_t)+gte(t-prev_selected_t,{interval})',{scale},showinfo[iv]"
    )
    cmd = [
        _ffmpeg_bin(ffmpeg_bin),
        "-y",
        "-nostdin",
        "-nostats",
        "-v",
        "info",
        "-i",
        str(src),
        "-filter_complex",
        graph,
        "-map",
        "[sc]",
        "-fps_mode",
        "vfr",
        "-an",
        "-q:v",
        "2",
        "-f",
        "image2",
        str(tmp_dir / "scene_%05d.jpg"),
        "-map",
        "[iv]",
        "-fps_mode",
        "vfr",
        "-an",
        "-q:v",
        "2",
        "-f",
        "image2",
        str(tmp_dir / "interval_%05d.jpg"),
    ]
    proc = _run(cmd, what="ffmpeg extract_keyframes")
    groups = _parse_showinfo(proc.stderr)
    ordered = [groups[k] for k in sorted(groups)]
    scene_ts = ordered[0] if len(ordered) >= 1 else []
    interval_ts = ordered[1] if len(ordered) >= 2 else []
    # When one branch selected nothing its showinfo never prints; disambiguate by file counts.
    n_scene = len(list(tmp_dir.glob("scene_*.jpg")))
    n_interval = len(list(tmp_dir.glob("interval_*.jpg")))
    if len(ordered) == 1 and n_scene == 0 and n_interval > 0:
        scene_ts, interval_ts = [], ordered[0]
    return _collect(tmp_dir, "scene", scene_ts, "scene") + _collect(tmp_dir, "interval", interval_ts, "interval")


def _dedupe(candidates: list[_Candidate], threshold: int = DEDUP_HAMMING_THRESHOLD) -> list[_Candidate]:
    """Sort by time, drop interval frames that coincide with a scene frame, then drop near-duplicates."""
    # Scene frames win over interval frames taken within half a second of them.
    candidates.sort(key=lambda c: (c.timestamp_sec, 0 if c.reason == "scene" else 1))
    merged: list[_Candidate] = []
    for cand in candidates:
        if merged and cand.reason == "interval" and abs(cand.timestamp_sec - merged[-1].timestamp_sec) < 0.5:
            continue
        merged.append(cand)
    kept: list[_Candidate] = []
    for cand in merged:
        if kept and hamming_distance(cand.phash, kept[-1].phash) <= threshold:
            continue
        kept.append(cand)
    return kept


def _spread(items: list[_Candidate], count: int) -> list[_Candidate]:
    """Pick ``count`` items evenly spread over the (time-sorted) list, always keeping first and last."""
    if count <= 0:
        return []
    if len(items) <= count:
        return list(items)
    if count == 1:
        return [items[0]]
    step = (len(items) - 1) / (count - 1)
    picked_idx = sorted({round(i * step) for i in range(count)})
    return [items[i] for i in picked_idx]


def select_spread(candidates: list[_Candidate], max_frames: int) -> list[_Candidate]:
    """Cap to ``max_frames`` preferring scene frames spread over time; fill the rest with interval frames."""
    if max_frames <= 0 or len(candidates) <= max_frames:
        return sorted(candidates, key=lambda c: c.timestamp_sec)
    scene = [c for c in candidates if c.reason == "scene"]
    interval = [c for c in candidates if c.reason != "scene"]
    chosen = _spread(scene, max_frames)
    remaining = max_frames - len(chosen)
    if remaining > 0:
        chosen += _spread(interval, remaining)
    return sorted(chosen, key=lambda c: c.timestamp_sec)


def spread_indices(count: int, max_count: int) -> list[int]:
    """Indices of ``max_count`` items evenly spread over ``range(count)`` (used for ``selected_for_llm``)."""
    if count <= 0 or max_count <= 0:
        return []
    if count <= max_count:
        return list(range(count))
    if max_count == 1:
        return [0]
    step = (count - 1) / (max_count - 1)
    return sorted({round(i * step) for i in range(max_count)})


def _clear_previous_frames(out_dir: Path) -> None:
    for f in out_dir.iterdir():
        if f.is_file() and _FRAME_FILE_RE.match(f.name):
            f.unlink(missing_ok=True)


def extract_keyframes(
    src: Path,
    out_dir: Path,
    *,
    duration_sec: float,
    interval_sec: int,
    scene_threshold: float,
    max_frames: int,
    ffmpeg_bin: str | None = None,
    ffprobe_bin: str | None = None,
) -> list[ExtractedFrame]:
    """Extract deduplicated keyframes of ``src`` into ``out_dir/NNNN.jpg`` and return them in time order.

    Args:
        src: source video (or audio — then an empty list is returned).
        out_dir: destination directory; previous ``NNNN.jpg`` files are removed first (reprocessing).
        duration_sec: media duration from :func:`probe_media` (used for logging / sanity checks).
        interval_sec: periodic sampling interval.
        scene_threshold: ffmpeg ``scene`` score threshold (0..1), e.g. 0.30.
        max_frames: hard cap on returned frames.
    """
    src, out_dir = Path(src), Path(out_dir)
    if not src.is_file():
        raise MediaError(f"extract_keyframes: file not found: {src}")
    info = probe_media(src, ffprobe_bin=ffprobe_bin)
    out_dir.mkdir(parents=True, exist_ok=True)
    _clear_previous_frames(out_dir)
    if not info.has_video:
        log.info("keyframes_skipped_audio_only", src=str(src.name))
        return []

    tmp_dir = Path(tempfile.mkdtemp(prefix="llframes-", dir=str(out_dir)))
    try:
        candidates = _run_extraction(
            src, tmp_dir, interval_sec=interval_sec, scene_threshold=scene_threshold, ffmpeg_bin=ffmpeg_bin
        )
        for cand in candidates:
            with Image.open(cand.tmp_path) as im:
                cand.phash = dhash(im)
        deduped = _dedupe(candidates)
        chosen = select_spread(deduped, max_frames)
        frames: list[ExtractedFrame] = []
        for idx, cand in enumerate(chosen):
            dst = out_dir / f"{idx:04d}.jpg"
            with Image.open(cand.tmp_path) as im:
                rgb = im.convert("RGB")
                if rgb.width > MAX_FRAME_WIDTH:
                    new_h = max(1, round(rgb.height * MAX_FRAME_WIDTH / rgb.width))
                    rgb = rgb.resize((MAX_FRAME_WIDTH, new_h), Image.Resampling.LANCZOS)
                rgb.save(dst, format="JPEG", quality=JPEG_QUALITY, optimize=True)
                frames.append(
                    ExtractedFrame(
                        index=idx,
                        timestamp_sec=cand.timestamp_sec,
                        path=dst,
                        width=rgb.width,
                        height=rgb.height,
                        phash=cand.phash,
                        reason=cand.reason,
                    )
                )
        log.info(
            "keyframes_extracted",
            duration_sec=duration_sec,
            candidates=len(candidates),
            after_dedup=len(deduped),
            kept=len(frames),
            scene=sum(1 for f in frames if f.reason == "scene"),
        )
        return frames
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
