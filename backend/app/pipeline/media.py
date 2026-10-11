"""Media processing with ffmpeg/ffprobe and Pillow: probing, audio extraction, keyframe extraction.

All functions are synchronous and CPU/IO bound — the pipeline runner calls them through
``asyncio.to_thread`` so the event loop stays responsive in ``PIPELINE_MODE=inline``.

Keyframe strategy (docs/CONTRACT.md §1 step 4, details in the "keyframes" section below): a cheap
low-resolution scan of the stream's keyframes (sampled decode only when keyframes are sparse) decides *which*
moments matter — new slides ("scene") and visible edits such as typed answers ("interval") — then only those
≤ ``max_frames`` stills are decoded at full resolution (max width 1280, JPEG quality 85), in parallel.

Audio-only inputs yield zero frames and no error. ffmpeg failures raise :class:`MediaError`
carrying the tail of stderr.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import numpy as np
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
    """Extract the audio track of ``src`` into ``dst`` as 16 kHz mono PCM WAV (input of local Whisper)."""
    src, dst = Path(src), Path(dst)
    if not src.is_file():
        raise MediaError(f"extract_audio: file not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Per-run temp name: two runs on the same lesson must never share (and delete) each other's partial file.
    tmp = dst.with_name(f"{dst.stem}.{os.getpid()}-{secrets.token_hex(4)}.tmp.wav")
    try:
        _run(
            [
                _ffmpeg_bin(ffmpeg_bin), "-y", "-nostdin", "-v", "error", "-i", str(src),
                "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav", str(tmp),
            ],
            what="ffmpeg extract_audio",
        )  # fmt: skip
        if not tmp.exists() or tmp.stat().st_size <= 44:
            raise MediaError("ffmpeg extract_audio: produced an empty wav (no audio stream?)")
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)
    return dst


# Source audio codecs every cloud STT API accepts as-is: copied into a matching container, no re-encoding.
_COPY_CONTAINERS: dict[str, tuple[str, str]] = {
    "aac": (".m4a", "ipod"),
    "mp3": (".mp3", "mp3"),
    "opus": (".ogg", "ogg"),
    "vorbis": (".ogg", "ogg"),
    "flac": (".flac", "flac"),
}
COMPACT_AUDIO_BITRATE = "32k"


def extract_compact_audio(
    src: Path, dst_dir: Path, *, audio_codec: str | None, stem: str = "audio", ffmpeg_bin: str | None = None
) -> Path:
    """Write the audio track for upload to a cloud STT provider and return its path (``dst_dir/audio.<ext>``).

    Zoom/Meet recordings carry AAC (sometimes Opus/MP3): that stream is *copied* into an ``.m4a``/``.ogg``/
    ``.mp3`` file — ~1–2 s for a 45-minute lesson, no quality loss, ~5x smaller than 16 kHz PCM. Any other codec
    is re-encoded to 16 kHz mono Ogg/Opus at :data:`COMPACT_AUDIO_BITRATE` with the fastest encoder setting
    (~20 s for 45 minutes on one core). Previous ``audio.*`` upload copies are removed (reprocessing).
    """
    src, dst_dir = Path(src), Path(dst_dir)
    if not src.is_file():
        raise MediaError(f"extract_compact_audio: file not found: {src}")
    dst_dir.mkdir(parents=True, exist_ok=True)
    copy = _COPY_CONTAINERS.get((audio_codec or "").lower())
    ext, fmt = copy or (".ogg", "ogg")
    dst = dst_dir / f"{stem}{ext}"
    tmp = dst_dir / f"{stem}.{os.getpid()}-{secrets.token_hex(4)}.tmp{ext}"
    codec_args = ["-c:a", "copy"] if copy else [
        "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", COMPACT_AUDIO_BITRATE,
        "-application", "voip", "-compression_level", "0",
    ]  # fmt: skip
    try:
        _run(
            [_ffmpeg_bin(ffmpeg_bin), "-y", "-nostdin", "-v", "error", "-i", str(src), "-map", "0:a:0", "-vn",
             *codec_args, "-f", fmt, str(tmp)],
            what="ffmpeg extract_compact_audio",
        )  # fmt: skip
        if not tmp.exists() or tmp.stat().st_size == 0:
            raise MediaError("ffmpeg extract_compact_audio: produced an empty file (no audio stream?)")
        for old in dst_dir.glob(f"{stem}.*"):
            if old.suffix in {".m4a", ".mp3", ".ogg", ".flac"} and ".tmp" not in old.name and old != tmp:
                old.unlink(missing_ok=True)
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)
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


# ---------------------------------------------------------------- keyframes (fast path)
#
# Old approach: one ffmpeg pass that decodes EVERY frame at full resolution and runs the ``scene`` filter on it.
# For a 45-minute 1080p Zoom recording that is ~67 000 full-size frames — minutes of CPU per lesson.
#
# New approach (two cheap passes):
#   1. Scan: decode only the stream's keyframes (``-skip_frame nokey``) scaled down to SCAN_W x SCAN_H grey.
#      Encoders put a keyframe at least every few seconds and on hard cuts (slide changes), so this sees every
#      slide at a tiny fraction of the cost. When keyframes are too sparse (long-GOP screen recordings), fall back
#      to a sampled full decode (``fps`` filter, loop filter skipped) — still low-resolution.
#   2. Grab: decode full-resolution stills only for the chosen ≤ max_frames timestamps, with fast input seeking,
#      several ffmpeg processes in parallel.
#
# Selection works on the scan images: pixels that keep changing (webcam tiles, video playback) are masked out, a
# large change of the static area is a "scene" (new slide/board), a small persistent change is an "edit" (the
# student typed or the teacher wrote on the board). Scene frames are taken right after the change; edits are
# sampled at most every ``interval_sec``, and the last state before every scene change is kept so finished
# exercises (with the student's mistakes) are always visible to the analysis.

SCAN_W, SCAN_H = 320, 180
PIXEL_DIFF = 18  # grey-level difference that counts as "changed" on the downscaled scan
DYNAMIC_PIXEL_SHARE = 0.10  # changing in ≥ 10 % of slide-stable scan pairs → live content (webcam, video)
MASK_DILATE_PX = 3
EDIT_SHARE = 0.0005  # ≥ 0.05 % of static pixels changed → content edit (≈ a typed word at 1080p)
MAX_SCAN_SAMPLES = 3600  # sampled-decode fallback never keeps more scan images than this
STILL_WORKERS = 4
SCAN_PARTS = max(1, min(4, os.cpu_count() or 1))
SCAN_PARALLEL_MIN_SEC = 300.0  # shorter recordings are scanned by a single process


def _scan_max_gap_sec() -> float:
    """Largest keyframe gap (s) for which the keyframe-only scan is trusted (``KEYFRAME_SCAN_MAX_GAP_SEC``)."""
    try:
        return float(os.environ.get("KEYFRAME_SCAN_MAX_GAP_SEC", "20"))
    except ValueError:
        return 20.0


@dataclass(slots=True)
class _Scan:
    times: list[float]
    images: np.ndarray  # (n, SCAN_H, SCAN_W) uint8
    mode: str  # "keyframes" | "sampled"


def _scan_part(
    src: Path, *, mode: str, fps: float, start: float, length: float | None, threads: int, ffmpeg_bin: str | None
) -> tuple[list[float], np.ndarray]:
    """Decode one time range of the scan; timestamps are returned relative to the start of the recording."""
    vf = [f"scale={SCAN_W}:{SCAN_H}:flags=area", "format=gray", "showinfo"]
    if mode == "keyframes":
        pre = ["-skip_frame", "nokey"]
    else:
        # 1 fps sampling needs no non-reference frames and no deblocking — both cut decode time substantially.
        pre = ["-skip_loop_filter", "all", "-skip_frame", "noref"]
        vf.insert(0, f"fps={fps:.4f}")
    rng = ["-ss", f"{start:.3f}"] if start > 0 else []
    if length is not None:
        rng += ["-t", f"{length:.3f}"]
    cmd = [
        _ffmpeg_bin(ffmpeg_bin), "-nostdin", "-nostats", "-v", "info", "-threads", str(threads),
        *pre, *rng, "-i", str(src),
        "-map", "0:v:0", "-an", "-sn", "-vf", ",".join(vf), "-fps_mode", "passthrough",
        "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ]
    log.debug("media_cmd", what=f"scan_{mode}", start=start, argv=cmd[:16])
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=_ffmpeg_timeout(), check=False)
    except FileNotFoundError as exc:
        raise MediaError(f"scan: binary not found: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"scan: timed out after {_ffmpeg_timeout()}s") from exc
    stderr = proc.stderr.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise MediaError(f"ffmpeg scan ({mode}): exit code {proc.returncode}: {_tail(stderr)}")
    frame_bytes = SCAN_W * SCAN_H
    times = [t + start for ts in _parse_showinfo(stderr).values() for t in ts]
    n = min(len(proc.stdout) // frame_bytes, len(times))
    images = np.frombuffer(proc.stdout[: n * frame_bytes], dtype=np.uint8).reshape(n, SCAN_H, SCAN_W)
    return times[:n], images


def _scan(src: Path, *, mode: str, duration_sec: float, ffmpeg_bin: str | None) -> _Scan:
    """Decode a low-resolution grey image stream with timestamps (see the section notes).

    Long recordings are split into :data:`SCAN_PARTS` time ranges decoded by parallel ffmpeg processes
    (one decoder thread each): ~2.5x faster than one multi-threaded decoder on 4 vCPU.
    """
    fps = 1.0 if duration_sec <= 0 else min(1.0, MAX_SCAN_SAMPLES / duration_sec)
    parts = SCAN_PARTS if duration_sec >= SCAN_PARALLEL_MIN_SEC else 1
    if parts == 1:
        times, images = _scan_part(
            src, mode=mode, fps=fps, start=0.0, length=None, threads=0, ffmpeg_bin=ffmpeg_bin
        )
    else:
        step = duration_sec / parts
        ranges = [(i * step, None if i == parts - 1 else step) for i in range(parts)]
        with ThreadPoolExecutor(max_workers=parts) as pool:
            results = list(
                pool.map(
                    lambda r: _scan_part(
                        src, mode=mode, fps=fps, start=r[0], length=r[1], threads=1, ffmpeg_bin=ffmpeg_bin
                    ),
                    ranges,
                )
            )
        times, chunks = [], []
        for part_times, part_images in results:
            # input seeking may return a frame just before the range start: keep timestamps strictly increasing
            keep = [i for i, t in enumerate(part_times) if not times or t > times[-1] + 1e-3]
            times += [part_times[i] for i in keep]
            chunks.append(part_images[keep])
        images = np.concatenate(chunks) if chunks else np.zeros((0, SCAN_H, SCAN_W), dtype=np.uint8)
    return _Scan(times=[round(t, 3) for t in times], images=images, mode=mode)


def _keyframes_dense_enough(times: list[float], duration_sec: float) -> bool:
    if len(times) < 2:
        return False
    edges = [0.0, *times, max(duration_sec, times[-1])]
    return max(b - a for a, b in itertools.pairwise(edges)) <= _scan_max_gap_sec()


def scan_video(src: Path, *, duration_sec: float, ffmpeg_bin: str | None = None) -> _Scan:
    """Keyframe-only scan, or a sampled decode when keyframes are too sparse to see every slide."""
    scan = _scan(src, mode="keyframes", duration_sec=duration_sec, ffmpeg_bin=ffmpeg_bin)
    if _keyframes_dense_enough(scan.times, duration_sec):
        return scan
    log.info("keyframes_sparse_fallback", keyframes=len(scan.times), duration_sec=duration_sec)
    return _scan(src, mode="sampled", duration_sec=duration_sec, ffmpeg_bin=ffmpeg_bin)


def _static_mask(images: np.ndarray, scene_share: float) -> np.ndarray:
    """True for pixels that are NOT live content (webcam tiles, embedded video).

    Change frequency is counted only over scan pairs without a slide change (otherwise every pixel of every slide
    would look "dynamic"); pixels changing in ≥ :data:`DYNAMIC_PIXEL_SHARE` of those pairs are dynamic, and the
    dynamic area is dilated so the edges of a moving tile are masked too.
    """
    ones = np.ones(images.shape[1:], dtype=bool)
    if len(images) < 8:
        return ones
    diffs = [np.abs(a.astype(np.int16) - b.astype(np.int16)) > PIXEL_DIFF for a, b in itertools.pairwise(images)]
    calm = [d for d in diffs if d.mean() < scene_share]
    if len(calm) < 5:
        return ones
    freq = np.mean(calm, axis=0)
    dynamic = freq >= DYNAMIC_PIXEL_SHARE
    grown = dynamic.copy()
    r = MASK_DILATE_PX
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            grown |= np.roll(np.roll(dynamic, dy, axis=0), dx, axis=1)
    mask = ~grown
    return mask if mask.mean() >= 0.2 else ones


def _changed_share(a: np.ndarray, b: np.ndarray, mask: np.ndarray, denom: int) -> float:
    return float(np.count_nonzero((np.abs(a.astype(np.int16) - b.astype(np.int16)) > PIXEL_DIFF) & mask)) / denom


def plan_keyframes(scan: _Scan, *, interval_sec: float, scene_share: float) -> list[tuple[float, str]]:
    """Pick ``(timestamp, reason)`` candidates from a scan (see the section notes). Time-ordered, no duplicates.

    A slide transition (fade, wipe, animation) spans several scans with large changes: it counts as ONE scene and
    the frame is taken once the picture has settled, not mid-transition.
    """
    n = len(scan.times)
    if n == 0:
        return []
    mask = _static_mask(scan.images, scene_share)
    denom = max(1, int(mask.sum()))
    steps = [0.0] + [_changed_share(scan.images[i - 1], scan.images[i], mask, denom) for i in range(1, n)]
    picks: list[tuple[int, str]] = [(0, "interval")]
    ref = 0  # index of the last picked image; edits are measured against it
    i = 1
    while i < n:
        if steps[i] >= scene_share:
            # keep the final state of the previous slide when it differs from what we already have
            prev = i - 1
            if picks[-1][0] != prev and _changed_share(scan.images[ref], scan.images[prev], mask, denom) >= EDIT_SHARE:
                picks.append((prev, "interval"))
            settled = i
            while settled + 1 < n and steps[settled + 1] >= scene_share:
                settled += 1
            picks.append((settled, "scene"))
            ref = settled
            i = settled + 1
            continue
        due = scan.times[i] - scan.times[ref] >= interval_sec
        if due and _changed_share(scan.images[ref], scan.images[i], mask, denom) >= EDIT_SHARE:
            picks.append((i, "interval"))
            ref = i
        i += 1
    last = n - 1
    if picks[-1][0] != last and _changed_share(scan.images[ref], scan.images[last], mask, denom) >= EDIT_SHARE:
        picks.append((last, "interval"))
    return [(scan.times[k], reason) for k, reason in picks]


def _grab_still(src: Path, t: float, dst: Path, ffmpeg_bin: str | None) -> None:
    _run(
        [
            _ffmpeg_bin(ffmpeg_bin),
            "-y",
            "-nostdin",
            "-v",
            "error",
            "-ss",
            f"{t:.3f}",
            "-i",
            str(src),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-vf",
            f"scale='min({MAX_FRAME_WIDTH},iw)':-2",
            "-q:v",
            "2",
            str(dst),
        ],
        what="ffmpeg grab_still",
    )
    if not dst.exists():
        raise MediaError(f"ffmpeg grab_still: no frame at {t:.3f}s")


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
    has_video: bool | None = None,
) -> list[ExtractedFrame]:
    """Extract keyframes of ``src`` into ``out_dir/NNNN.jpg`` and return them in time order.

    Args:
        src: source video (or audio — then an empty list is returned).
        out_dir: destination directory; previous ``NNNN.jpg`` files are removed first (reprocessing).
        duration_sec: media duration from :func:`probe_media`.
        interval_sec: minimum spacing of "edit" frames inside one slide.
        scene_threshold: share (0..1) of static pixels that must change between two scans to count as a new
            slide/scene, e.g. 0.10.
        max_frames: hard cap on returned frames (scene frames preferred, spread over the recording).
        has_video: pass ``MediaInfo.has_video`` to skip the extra ffprobe call.
    """
    src, out_dir = Path(src), Path(out_dir)
    if not src.is_file():
        raise MediaError(f"extract_keyframes: file not found: {src}")
    if has_video is None or duration_sec <= 0:
        info = probe_media(src, ffprobe_bin=ffprobe_bin)
        has_video, duration_sec = info.has_video, info.duration_sec
    out_dir.mkdir(parents=True, exist_ok=True)
    _clear_previous_frames(out_dir)
    if not has_video:
        log.info("keyframes_skipped_audio_only", src=str(src.name))
        return []

    scan = scan_video(src, duration_sec=duration_sec, ffmpeg_bin=ffmpeg_bin)
    planned = plan_keyframes(scan, interval_sec=max(1, int(interval_sec)), scene_share=scene_threshold)
    candidates = [_Candidate(timestamp_sec=t, reason=r, tmp_path=Path()) for t, r in planned]
    chosen = select_spread(candidates, max_frames)

    tmp_dir = Path(tempfile.mkdtemp(prefix="llframes-", dir=str(out_dir)))
    try:
        for i, cand in enumerate(chosen):
            cand.tmp_path = tmp_dir / f"{i:04d}.jpg"
        with ThreadPoolExecutor(max_workers=min(STILL_WORKERS, max(1, len(chosen)))) as pool:
            list(pool.map(lambda c: _grab_still(src, c.timestamp_sec, c.tmp_path, ffmpeg_bin), chosen))
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
                        phash=dhash(rgb),
                        reason=cand.reason,
                    )
                )
        log.info(
            "keyframes_extracted",
            duration_sec=duration_sec,
            scan_mode=scan.mode,
            scanned=len(scan.times),
            candidates=len(candidates),
            kept=len(frames),
            scene=sum(1 for f in frames if f.reason == "scene"),
        )
        return frames
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
