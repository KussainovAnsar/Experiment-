"""Fast keyframe extraction: edits on one slide, webcam-tile masking, sparse-keyframe fallback, parallel scan."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app.pipeline import media
from app.pipeline.media import extract_keyframes, scan_video

LINES = ["1. Ya idu v shkolu.", "2. Kniga lezhit na stole.", "3. My poedem v Almaty.", "4. On hochet uchitsya."]


def _slide(n: int, *, title: str = "Exercise 4") -> Image.Image:
    im = Image.new("RGB", (960, 540), "white")
    d = ImageDraw.Draw(im)
    d.text((40, 30), title, fill="black", font_size=28)
    for i, line in enumerate(LINES[:n]):
        d.text((40, 110 + i * 50), line, fill="navy", font_size=24)
    return im


def _video(tmp: Path, timeline: list[tuple[float, Image.Image]], total: float, *, gop: int) -> Path:
    """Slides at given times + a moving 'webcam' tile in the corner + quiet audio."""
    lines = []
    for i, (t, im) in enumerate(timeline):
        p = tmp / f"s{i}.png"
        im.save(p)
        end = timeline[i + 1][0] if i + 1 < len(timeline) else total
        lines += [f"file '{p}'", f"duration {end - t}"]
    lines.append(f"file '{tmp / f's{len(timeline) - 1}.png'}'")
    (tmp / "c.txt").write_text("\n".join(lines) + "\n")
    out = tmp / "lesson.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "concat", "-safe", "0", "-i", str(tmp / "c.txt"),
            "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10",
            "-f", "lavfi", "-i", "anoisesrc=a=0.01:r=16000",
            "-filter_complex", "[0:v]fps=10,format=yuv420p[bg];[bg][1:v]overlay=W-w-12:12:shortest=1[v]",
            "-map", "[v]", "-map", "2:a", "-t", str(total),
            "-c:v", "libx264", "-preset", "ultrafast", "-g", str(gop), "-sc_threshold", "0", "-c:a", "aac", str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


@pytest.fixture(scope="module")
def typing_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("typing")
    timeline = [(0, _slide(0)), (20, _slide(1)), (40, _slide(2)), (60, _slide(3)), (80, _slide(4))]
    timeline.append((110, _slide(0, title="Summary")))
    # keyframe every 2 s and no scene-cut keyframes: slide changes must be found by the scan, not the encoder
    return _video(tmp, timeline, 130, gop=20)


def test_edits_on_one_slide_are_captured(typing_video: Path, tmp_path: Path) -> None:
    frames = extract_keyframes(
        typing_video, tmp_path / "f", duration_sec=130, interval_sec=10, scene_threshold=0.06, max_frames=40
    )
    times = [f.timestamp_sec for f in frames]
    # every typed line becomes visible in some frame (within one keyframe interval of the edit)
    for edit_t in (20, 40, 60, 80, 110):
        assert any(edit_t <= t <= edit_t + 4 for t in times), (edit_t, times)
    # the moving webcam tile must not create frames on static stretches: ≤ one extra frame per slide
    assert len(frames) <= 8, times


def test_sparse_keyframes_fall_back_to_sampled_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    timeline = [(0, _slide(0)), (30, _slide(2)), (60, _slide(4))]
    video = _video(tmp_path, timeline, 90, gop=900)  # one keyframe for the whole file
    scan = scan_video(video, duration_sec=90)
    assert scan.mode == "sampled"
    assert len(scan.times) >= 80
    frames = extract_keyframes(
        video, tmp_path / "f", duration_sec=90, interval_sec=10, scene_threshold=0.06, max_frames=40
    )
    times = [f.timestamp_sec for f in frames]
    assert any(30 <= t <= 32 for t in times) and any(60 <= t <= 62 for t in times), times


def test_parallel_scan_matches_single_process(typing_video: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    single = scan_video(typing_video, duration_sec=130)
    monkeypatch.setattr(media, "SCAN_PARALLEL_MIN_SEC", 0.0)
    monkeypatch.setattr(media, "SCAN_PARTS", 3)
    parallel = scan_video(typing_video, duration_sec=130)
    assert parallel.times == sorted(parallel.times)
    assert len(set(parallel.times)) == len(parallel.times)
    assert parallel.times == pytest.approx(single.times, abs=0.05)
