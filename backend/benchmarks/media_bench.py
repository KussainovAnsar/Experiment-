"""Time the media stages of the lesson pipeline on a real recording.

    python benchmarks/media_bench.py benchmarks/data/lesson_45m_1080p.mp4 --label baseline

Measures ffprobe, audio extraction and keyframe extraction exactly as the pipeline calls them
(app.pipeline.media) and appends a JSON line to benchmarks/results/media_bench.jsonl.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app.config import get_settings  # noqa: E402
from app.pipeline import media  # noqa: E402


def _t(fn, *a, **kw):
    t0 = time.perf_counter()
    out = fn(*a, **kw)
    return out, round(time.perf_counter() - t0, 2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--label", default="run")
    args = ap.parse_args()
    s = get_settings()
    work = Path(tempfile.mkdtemp(prefix="mbench-"))
    try:
        info, t_probe = _t(media.probe_media, args.video)
        _, t_audio = _t(media.extract_audio, args.video, work / "audio.wav")
        extra = {}
        if hasattr(media, "extract_audio_for_stt"):
            _, extra["audio_compact_sec"] = _t(media.extract_audio_for_stt, args.video, work)
        frames, t_frames = _t(
            media.extract_keyframes,
            args.video,
            work / "frames",
            duration_sec=info.duration_sec,
            interval_sec=s.keyframe_interval_sec,
            scene_threshold=s.keyframe_scene_threshold,
            max_frames=s.keyframe_max,
        )
        row = {
            "label": args.label,
            "video": args.video.name,
            "duration_sec": info.duration_sec,
            "resolution": f"{info.width}x{info.height}",
            "probe_sec": t_probe,
            "audio_sec": t_audio,
            "frames_sec": t_frames,
            "frames_kept": len(frames),
            "frame_times": [round(f.timestamp_sec) for f in frames],
            **extra,
        }
        print(json.dumps(row, ensure_ascii=False))
        out = HERE / "results" / "media_bench.jsonl"
        out.parent.mkdir(exist_ok=True)
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
