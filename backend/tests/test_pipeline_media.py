"""ffprobe / ffmpeg / keyframe extraction on a synthetic 20-second video (no external services)."""

from __future__ import annotations

import itertools
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from app.pipeline.media import (
    DEDUP_HAMMING_THRESHOLD,
    MediaError,
    dhash,
    extract_audio,
    extract_keyframes,
    hamming_distance,
    probe_media,
    spread_indices,
)

# smptebars (static, 8 s) → testsrc (animated, 6 s) → rgbtestsrc (static, 6 s): two hard cuts at 8 s and 14 s.
_VIDEO_GRAPH = (
    "smptebars=s=640x360:r=25:d=8[a];testsrc=s=640x360:r=25:d=6[b];rgbtestsrc=s=640x360:r=25:d=6[c];"
    "[a][b][c]concat=n=3:v=1:a=0"
)


@pytest.fixture(scope="module")
def test_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """20 s mp4 with three visually distinct parts and a 440 Hz sine audio track."""
    path = tmp_path_factory.mktemp("media") / "lesson.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            _VIDEO_GRAPH,
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:d=20",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    assert path.stat().st_size > 0
    return path


def test_probe_media(test_video: Path) -> None:
    info = probe_media(test_video)
    assert info.has_video and info.has_audio
    assert 19.5 <= info.duration_sec <= 20.5
    assert (info.width, info.height) == (640, 360)
    assert info.fps == pytest.approx(25.0, abs=0.1)
    assert info.video_codec == "h264"
    assert info.audio_codec == "aac"


def test_probe_media_missing_file(tmp_path: Path) -> None:
    with pytest.raises(MediaError):
        probe_media(tmp_path / "nope.mp4")


def test_probe_media_garbage(tmp_path: Path) -> None:
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"this is not a media file" * 100)
    with pytest.raises(MediaError):
        probe_media(bad)


def test_extract_audio_is_16k_mono(test_video: Path, tmp_path: Path) -> None:
    wav = extract_audio(test_video, tmp_path / "audio.wav")
    assert wav.exists() and wav.stat().st_size > 44
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate,channels,codec_name",
            "-of",
            "csv=p=0",
            str(wav),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert out.split(",") == ["pcm_s16le", "16000", "1"]
    info = probe_media(wav)
    assert not info.has_video and info.has_audio
    assert info.duration_sec == pytest.approx(20.0, abs=0.2)


def test_extract_audio_failure(tmp_path: Path) -> None:
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"\x00" * 1024)
    with pytest.raises(MediaError) as exc_info:
        extract_audio(bad, tmp_path / "audio.wav")
    assert "ffmpeg" in str(exc_info.value)


def test_extract_keyframes_scene_and_interval_with_dedup(test_video: Path, tmp_path: Path) -> None:
    out = tmp_path / "frames"
    frames = extract_keyframes(test_video, out, duration_sec=20.0, interval_sec=2, scene_threshold=0.3, max_frames=40)
    # 2 scene cuts + ~10 interval candidates; the static parts collapse to one frame each.
    assert 2 <= len(frames) <= 6
    assert [f.index for f in frames] == list(range(len(frames)))
    assert all(f.path == out / f"{f.index:04d}.jpg" and f.path.exists() for f in frames)
    assert all(len(f.phash) == 16 for f in frames)
    timestamps = [f.timestamp_sec for f in frames]
    assert timestamps == sorted(timestamps)
    assert any(f.reason == "scene" for f in frames)
    scene_ts = [f.timestamp_sec for f in frames if f.reason == "scene"]
    assert any(abs(t - 8.0) < 0.5 for t in scene_ts), scene_ts
    assert any(abs(t - 14.0) < 0.5 for t in scene_ts), scene_ts
    # Dedup: no two consecutive kept frames are near-duplicates.
    for a, b in itertools.pairwise(frames):
        assert hamming_distance(a.phash, b.phash) > DEDUP_HAMMING_THRESHOLD
    with Image.open(frames[0].path) as im:
        assert im.format == "JPEG" and im.width <= 1280
        assert (im.width, im.height) == (frames[0].width, frames[0].height)
    # Only NNNN.jpg files remain (temp dir cleaned).
    assert sorted(p.name for p in out.iterdir()) == [f"{i:04d}.jpg" for i in range(len(frames))]


def test_extract_keyframes_cap_prefers_scene(test_video: Path, tmp_path: Path) -> None:
    frames = extract_keyframes(
        test_video, tmp_path / "frames", duration_sec=20.0, interval_sec=1, scene_threshold=0.3, max_frames=2
    )
    assert len(frames) == 2
    assert all(f.reason == "scene" for f in frames)


def test_extract_keyframes_reprocess_clears_old(test_video: Path, tmp_path: Path) -> None:
    out = tmp_path / "frames"
    out.mkdir()
    stale = out / "0042.jpg"
    stale.write_bytes(b"stale")
    frames = extract_keyframes(test_video, out, duration_sec=20.0, interval_sec=5, scene_threshold=0.3, max_frames=40)
    assert not stale.exists()
    assert len(frames) >= 2


def test_extract_keyframes_audio_only_returns_empty(test_video: Path, tmp_path: Path) -> None:
    wav = extract_audio(test_video, tmp_path / "audio.wav")
    frames = extract_keyframes(
        wav, tmp_path / "frames", duration_sec=20.0, interval_sec=2, scene_threshold=0.3, max_frames=40
    )
    assert frames == []


def test_dhash_and_hamming() -> None:
    red = Image.new("RGB", (64, 48), (200, 20, 20))
    assert dhash(red) == "0" * 16
    # linear_gradient is dark→light top→bottom; rotated 270° it is dark→light right→left: every left>right bit = 1.
    gradient = Image.linear_gradient("L").rotate(270).resize((64, 48))
    h = dhash(gradient)
    assert h == "f" * 16
    flipped = dhash(gradient.rotate(180))
    assert flipped == "0" * 16
    assert hamming_distance(h, h) == 0
    assert hamming_distance(h, flipped) == 64 > DEDUP_HAMMING_THRESHOLD
    checker = Image.effect_noise((64, 48), 80)
    assert dhash(checker) not in (h, flipped)


def test_spread_indices() -> None:
    assert spread_indices(3, 10) == [0, 1, 2]
    assert spread_indices(10, 3) == [0, 4, 9]
    assert spread_indices(10, 1) == [0]
    assert spread_indices(0, 5) == []
