"""Build a realistic long lesson recording for pipeline speed benchmarks.

Zoom-like layout: the demo screen-share slides change at (scaled) real lesson timestamps, a moving
"webcam" tile (ffmpeg testsrc2) keeps the encoder/decoder busy like a real camera, and the audio track is
real synthetic speech (benchmarks/data/clean/**.wav, made by make_dataset.py) separated by pauses.

    python benchmarks/make_lesson_video.py --minutes 45 --height 1080 --out benchmarks/data/lesson_45m_1080p.mp4

The output is gitignored (benchmarks/data). Encoding a 45-minute 1080p file takes several minutes on 4 vCPU.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
FRAMES_DIR = REPO / "demo" / "lesson_01" / "frames"
SPEECH_DIR = HERE / "data" / "clean"
SR = 16000


def _read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        if w.getframerate() != SR or w.getnchannels() != 1:
            raise ValueError(f"{path}: expected 16 kHz mono")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def build_speech(total_sec: float, out: Path, seed: int = 7) -> None:
    clips = sorted(SPEECH_DIR.rglob("*.wav"))
    if not clips:
        raise SystemExit(f"no speech clips in {SPEECH_DIR}; run benchmarks/make_dataset.py first")
    rng = random.Random(seed)
    audio = [_read_wav(p) for p in clips]
    parts: list[np.ndarray] = []
    n = 0
    target = int(total_sec * SR)
    while n < target:
        clip = audio[rng.randrange(len(audio))]
        gap = np.zeros(int(rng.uniform(0.3, 2.5) * SR), dtype=np.int16)  # natural pauses incl. long ones
        if rng.random() < 0.05:
            gap = np.zeros(int(rng.uniform(5, 15) * SR), dtype=np.int16)  # student thinking / typing
        parts += [clip, gap]
        n += len(clip) + len(gap)
    pcm = np.concatenate(parts)[:target]
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=45)
    ap.add_argument("--height", type=int, default=1080, choices=[720, 1080])
    ap.add_argument("--fps", type=int, default=25)
    ap.add_argument("--out", type=Path, default=HERE / "data" / "lesson_45m_1080p.mp4")
    args = ap.parse_args()
    total = args.minutes * 60
    w, h = (1920, 1080) if args.height == 1080 else (1280, 720)
    frames = json.loads((FRAMES_DIR / "frames.json").read_text(encoding="utf-8"))
    src_total = 2100.0
    scale = total / src_total
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        concat = tdp / "slides.txt"
        lines = []
        for i, f in enumerate(frames):
            start = 0.0 if i == 0 else f["timestamp_sec"] * scale
            end = frames[i + 1]["timestamp_sec"] * scale if i + 1 < len(frames) else total
            lines += [f"file '{FRAMES_DIR / f['file']}'", f"duration {max(0.04, end - start):.3f}"]
        lines.append(f"file '{FRAMES_DIR / frames[-1]['file']}'")
        concat.write_text("\n".join(lines) + "\n", encoding="utf-8")
        speech = tdp / "speech.wav"
        build_speech(total, speech)
        tile_w, tile_h = w // 6, h // 6
        graph = (
            f"[0:v]scale={w}:{h},fps={args.fps},format=yuv420p[bg];"
            f"[1:v]scale={tile_w}:{tile_h}[cam];"
            f"[bg][cam]overlay=W-w-24:24:shortest=1[v]"
        )
        cmd = [
            "ffmpeg", "-y", "-v", "error", "-stats",
            "-f", "concat", "-safe", "0", "-i", str(concat),
            "-f", "lavfi", "-i", f"testsrc2=size={tile_w}x{tile_h}:rate={args.fps}",
            "-i", str(speech),
            "-filter_complex", graph, "-map", "[v]", "-map", "2:a",
            "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-g", "250", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "64k", "-ar", "32000",
            "-movflags", "+faststart",
            str(args.out),
        ]
        subprocess.run(cmd, check=True)
    print(args.out, args.out.stat().st_size // (1024 * 1024), "MB")


if __name__ == "__main__":
    main()
