"""End-to-end wall clock of the lesson pipeline on a real recording (real DB, ffmpeg, reports, progress).

Speech recognition and the LLM are the mock providers plus an optional simulated latency, because their speed
depends on the chosen provider (see docs/PERFORMANCE.md for measured/assumed values):

    python benchmarks/pipeline_bench.py benchmarks/data/lesson_45m_1080p.mp4 --stt-delay 0 --llm-delay 0
    python benchmarks/pipeline_bench.py benchmarks/data/lesson_45m_1080p.mp4 --stt-delay 40 --llm-delay 75

Creates a throw-away centre in the configured database, prints ``lessons.processing_stats`` and deletes it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ.setdefault("STT_PROVIDER", "mock")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("MESSAGING_PROVIDER", "mock")

from sqlalchemy import delete  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.models import Center, Lesson, MediaKind, Student, Teacher  # noqa: E402
from app.db.session import get_engine, get_sessionmaker  # noqa: E402
from app.pipeline import runner  # noqa: E402
from app.providers.llm import registry as llm_registry  # noqa: E402
from app.providers.stt.registry import get_stt_provider  # noqa: E402


class _Delayed:
    def __init__(self, inner, delay: float, method: str, *, compact: bool = True) -> None:  # noqa: ANN001
        self.inner, self.delay, self.method = inner, delay, method
        self.name = getattr(inner, "name", "mock")
        self.prefers_compact_audio = compact

    def __getattr__(self, item: str):  # noqa: ANN204
        return getattr(self.inner, item)

    async def transcribe(self, *a, **kw):  # noqa: ANN002, ANN003, ANN201
        await asyncio.sleep(self.delay)
        return await self.inner.transcribe(*a, **kw)

    async def analyze(self, *a, **kw):  # noqa: ANN002, ANN003, ANN201
        await asyncio.sleep(self.delay)
        return await self.inner.analyze(*a, **kw)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--stt-delay", type=float, default=0.0)
    ap.add_argument("--llm-delay", type=float, default=0.0)
    ap.add_argument("--audio", choices=["cloud", "local"], default="cloud",
                    help="cloud: copied source track (as Deepgram/AssemblyAI/ElevenLabs get it); local: 16 kHz WAV")
    args = ap.parse_args()
    settings = get_settings()
    stt = get_stt_provider(settings)
    compact = args.audio == "cloud"
    runner.get_stt_provider = lambda s: _Delayed(stt, args.stt_delay, "transcribe", compact=compact)  # type: ignore[assignment]
    real_llm = llm_registry.get_llm_provider
    import app.pipeline.analysis as analysis

    analysis.get_llm_provider = lambda s: _Delayed(real_llm(s), args.llm_delay, "analyze")  # type: ignore[assignment]

    sm = get_sessionmaker()
    async with sm() as session:
        center = Center(name="bench", city="bench")
        session.add(center)
        await session.flush()
        teacher = Teacher(center_id=center.id, full_name="T", subjects=["russian"])
        student = Student(center_id=center.id, full_name="S", grade=6)
        session.add_all([teacher, student])
        await session.flush()
        lesson = Lesson(
            center_id=center.id, teacher_id=teacher.id, student_id=student.id, scheduled_at=datetime.now(UTC),
            media_kind=MediaKind.video, consent_confirmed=True, language_hints=["ru", "kk"],
            source_filename=args.video.name,
        )  # fmt: skip
        session.add(lesson)
        await session.flush()
        dst = settings.media_root / str(center.id) / str(lesson.id) / f"source{args.video.suffix}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.video, dst)
        lesson.source_path = str(dst)
        await session.commit()
    try:
        t0 = time.monotonic()
        result = await runner.run_lesson_pipeline_detailed(lesson.id)
        wall = round(time.monotonic() - t0, 1)
        async with sm() as session:
            fresh = await session.get(Lesson, lesson.id)
            print(json.dumps({"status": result.status.value if result.status else None,
                              "audio": args.audio, "stt_delay": args.stt_delay, "llm_delay": args.llm_delay,
                              "wall_sec": wall, "stats": getattr(fresh, "processing_stats", None),
                              "error": fresh.error}, ensure_ascii=False))  # fmt: skip
    finally:
        async with sm() as session:
            await session.execute(delete(Center).where(Center.id == center.id))
            await session.commit()
        shutil.rmtree(settings.media_root / str(center.id), ignore_errors=True)
        await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
