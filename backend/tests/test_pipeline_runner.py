"""End-to-end pipeline runner with mock STT and stubbed B2/C stages (standalone DB fixtures)."""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.base import Base
from app.db.models import (
    Analysis,
    Center,
    Frame,
    Job,
    Lesson,
    LessonStatus,
    MediaKind,
    Student,
    Teacher,
    Transcript,
)
from app.pipeline import runner
from app.pipeline.runner import enqueue_lesson_processing, run_lesson_pipeline, run_lesson_pipeline_result
from app.worker import run_once

TEST_DB_URL = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://lessonlens:lessonlens@127.0.0.1:5432/lessonlens_test"
)
MINIMAL_ANALYSIS = {
    "schema_version": "1.0",
    "subject": "Русский язык",
    "topic": "Тест",
    "lesson_summary": "Тестовый анализ.",
    "manager_indicators": {
        "objectives_checked": False,
        "difficulties_detected": False,
        "re_explanation_happened": False,
        "needs_methodist_review": False,
    },
    "parent_report": {"topic_plain": "Тест", "encouragement": "Молодец!"},
}


# ---------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(TEST_DB_URL, pool_pre_ping=True)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="module")
def sm(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


_RUNNER_GRAPH = "smptebars=s=320x240:r=25:d=6[a];rgbtestsrc=s=320x240:r=25:d=6[b];[a][b]concat=n=2:v=1:a=0"


@pytest.fixture(scope="module")
def media_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """12 s video with one hard cut + sine audio."""
    path = tmp_path_factory.mktemp("runner-media") / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            _RUNNER_GRAPH,
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:sample_rate=16000:d=12",
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
    return path


@pytest.fixture
async def center(sm: async_sessionmaker[AsyncSession]) -> AsyncIterator[Center]:
    async with sm() as session:
        c = Center(name="Тестовый центр B1", city="Алматы")
        session.add(c)
        await session.commit()
    yield c
    async with sm() as session:
        await session.execute(delete(Center).where(Center.id == c.id))
        await session.commit()


async def _make_lesson(
    sm: async_sessionmaker[AsyncSession],
    center: Center,
    source: Path | None,
    *,
    media_kind: MediaKind = MediaKind.video,
) -> Lesson:
    async with sm() as session:
        teacher = Teacher(center_id=center.id, full_name="Учитель", subjects=["russian"])
        student = Student(center_id=center.id, full_name="Ученик", grade=5)
        session.add_all([teacher, student])
        await session.flush()
        lesson = Lesson(
            center_id=center.id,
            teacher_id=teacher.id,
            student_id=student.id,
            scheduled_at=datetime.now(UTC),
            source_filename=source.name if source else None,
            media_kind=media_kind,
            consent_confirmed=True,
            language_hints=["ru", "kk"],
        )
        session.add(lesson)
        await session.flush()
        if source is not None:
            # Copy into the lesson directory layout the API uses (MEDIA_ROOT/{center}/{lesson}/source.mp4).
            dst = get_settings().media_root / str(center.id) / str(lesson.id) / f"source{source.suffix}"
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(source.read_bytes())
            lesson.source_path = str(dst)
        else:
            lesson.source_path = str(get_settings().media_root / str(center.id) / str(lesson.id) / "missing.mp4")
        await session.commit()
        return lesson


@pytest.fixture
def stub_hooks(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """Replace B2/C stage functions with recording stubs (so this test needs neither module)."""
    calls: dict[str, list] = {"analyze": [], "reports": [], "progress": [], "notify": []}

    async def analyze_lesson(session: AsyncSession, lesson: Lesson) -> Analysis:
        assert lesson.transcript is not None, "runner must selectinload transcript"
        assert isinstance(lesson.frames, list), "runner must selectinload frames"
        calls["analyze"].append((lesson.id, len(lesson.frames)))
        await session.execute(delete(Analysis).where(Analysis.lesson_id == lesson.id))
        analysis = Analysis(lesson_id=lesson.id, provider="mock", model="mock", data=MINIMAL_ANALYSIS)
        session.add(analysis)
        await session.flush()
        return analysis

    async def build_reports(session: AsyncSession, lesson: Lesson, analysis: Analysis) -> list:
        calls["reports"].append(lesson.id)
        return []

    async def update_progress(session: AsyncSession, lesson: Lesson, analysis: Analysis) -> list:
        calls["progress"].append(lesson.id)
        return []

    async def notify(session: AsyncSession, lesson_id: uuid.UUID) -> list:
        calls["notify"].append(lesson_id)
        raise RuntimeError("telegram is down")  # must not fail the lesson

    monkeypatch.setitem(runner.HOOK_OVERRIDES, "analyze_lesson", analyze_lesson)
    monkeypatch.setitem(runner.HOOK_OVERRIDES, "build_reports", build_reports)
    monkeypatch.setitem(runner.HOOK_OVERRIDES, "update_progress", update_progress)
    monkeypatch.setitem(runner.HOOK_OVERRIDES, "notify_parents_for_lesson", notify)
    return calls


# ---------------------------------------------------------------- tests


async def test_pipeline_video_lesson_reaches_done(
    sm: async_sessionmaker[AsyncSession], center: Center, media_file: Path, stub_hooks: dict[str, list]
) -> None:
    lesson = await _make_lesson(sm, center, media_file)
    await run_lesson_pipeline(lesson.id, sessionmaker=sm)

    async with sm() as session:
        fresh = (await session.execute(select(Lesson).where(Lesson.id == lesson.id))).scalar_one()
        assert fresh.status == LessonStatus.done, (fresh.status_message, fresh.error)
        assert fresh.error is None
        assert fresh.processed_at is not None
        assert fresh.status_message == "Обработка завершена"
        assert fresh.duration_sec == pytest.approx(12.0, abs=0.3)
        assert fresh.media_kind == MediaKind.video
        assert fresh.audio_path and Path(fresh.audio_path).name == "audio.wav" and Path(fresh.audio_path).exists()

        transcript = (await session.execute(select(Transcript).where(Transcript.lesson_id == lesson.id))).scalar_one()
        assert transcript.provider == "mock"
        assert transcript.word_count > 0 and transcript.full_text
        assert transcript.segments and transcript.segments[0]["speaker"] in ("teacher", "student")
        assert transcript.speaker_map

        stmt = select(Frame).where(Frame.lesson_id == lesson.id).order_by(Frame.index)
        frames = (await session.execute(stmt)).scalars().all()
        assert len(frames) >= 1
        assert [f.index for f in frames] == list(range(len(frames)))
        assert all(Path(f.path).exists() and Path(f.path).parent.name == "frames" for f in frames)
        assert all(f.selected_for_llm for f in frames)

    assert len(stub_hooks["analyze"]) == 1 and stub_hooks["analyze"][0][1] >= 1
    assert stub_hooks["reports"] == [lesson.id]
    assert stub_hooks["progress"] == [lesson.id]
    assert stub_hooks["notify"] == [lesson.id]  # called, raised, lesson still done


async def test_pipeline_reprocess_replaces_artefacts(
    sm: async_sessionmaker[AsyncSession], center: Center, media_file: Path, stub_hooks: dict[str, list]
) -> None:
    lesson = await _make_lesson(sm, center, media_file)
    await run_lesson_pipeline(lesson.id, sessionmaker=sm)
    await run_lesson_pipeline(lesson.id, sessionmaker=sm)
    async with sm() as session:
        stmt = select(Transcript).where(Transcript.lesson_id == lesson.id)
        transcripts = (await session.execute(stmt)).scalars().all()
        assert len(transcripts) == 1
        frames = (await session.execute(select(Frame).where(Frame.lesson_id == lesson.id))).scalars().all()
        assert len({f.index for f in frames}) == len(frames)
        fresh = await session.get(Lesson, lesson.id)
        assert fresh is not None and fresh.status == LessonStatus.done


async def test_pipeline_audio_only_skips_frames(
    sm: async_sessionmaker[AsyncSession], center: Center, media_file: Path, stub_hooks: dict[str, list], tmp_path: Path
) -> None:
    wav = tmp_path / "lesson.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(media_file), "-vn", "-ac", "1", "-ar", "16000", str(wav)],
        check=True,
        capture_output=True,
    )
    lesson = await _make_lesson(sm, center, wav, media_kind=MediaKind.audio)
    await run_lesson_pipeline(lesson.id, sessionmaker=sm)
    async with sm() as session:
        fresh = await session.get(Lesson, lesson.id)
        assert fresh is not None and fresh.status == LessonStatus.done, (fresh.status_message, fresh.error)
        assert fresh.media_kind == MediaKind.audio
        frames = (await session.execute(select(Frame).where(Frame.lesson_id == lesson.id))).scalars().all()
        assert frames == []
    assert stub_hooks["analyze"][0][1] == 0


async def test_pipeline_missing_source_fails_with_russian_message(
    sm: async_sessionmaker[AsyncSession], center: Center, stub_hooks: dict[str, list]
) -> None:
    lesson = await _make_lesson(sm, center, None)
    status = await run_lesson_pipeline_result(lesson.id, sessionmaker=sm)
    assert status == LessonStatus.failed
    async with sm() as session:
        fresh = await session.get(Lesson, lesson.id)
        assert fresh is not None
        assert fresh.status == LessonStatus.failed
        assert fresh.error and fresh.error.startswith("[probing] MediaError")
        assert fresh.status_message and "Не удалось обработать медиафайл" in fresh.status_message
    assert stub_hooks["analyze"] == []


async def test_pipeline_missing_dependency_fails_cleanly(
    sm: async_sessionmaker[AsyncSession], center: Center, media_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(runner.HOOKS, "analyze_lesson", ("app.pipeline.definitely_missing_module", "analyze_lesson"))
    monkeypatch.setitem(runner.HOOK_OVERRIDES, "analyze_lesson", None)
    lesson = await _make_lesson(sm, center, media_file)
    await run_lesson_pipeline(lesson.id, sessionmaker=sm)
    async with sm() as session:
        fresh = await session.get(Lesson, lesson.id)
        assert fresh is not None and fresh.status == LessonStatus.failed
        assert fresh.error and "[analyzing]" in fresh.error
        assert fresh.status_message and "Модуль обработки недоступен" in fresh.status_message
        # Earlier stages' artefacts are kept (partial progress committed).
        assert (await session.execute(select(Transcript).where(Transcript.lesson_id == lesson.id))).scalar_one()


async def test_pipeline_unknown_lesson_is_noop(sm: async_sessionmaker[AsyncSession]) -> None:
    assert await run_lesson_pipeline_result(uuid.uuid4(), sessionmaker=sm) == LessonStatus.failed


async def test_enqueue_inline_runs_in_background(
    sm: async_sessionmaker[AsyncSession],
    center: Center,
    media_file: Path,
    stub_hooks: dict[str, list],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "get_settings", lambda: get_settings().model_copy(update={"pipeline_mode": "inline"}))
    monkeypatch.setattr(runner, "get_sessionmaker", lambda: sm)
    lesson = await _make_lesson(sm, center, media_file)
    async with sm() as session:
        await enqueue_lesson_processing(session, lesson.id)
        await session.commit()
    assert runner._BACKGROUND_TASKS, "a background task must be scheduled"
    for _ in range(300):
        await asyncio.sleep(0.1)
        async with sm() as session:
            fresh = await session.get(Lesson, lesson.id)
            assert fresh is not None
            if fresh.status in (LessonStatus.done, LessonStatus.failed):
                break
    assert fresh.status == LessonStatus.done, (fresh.status_message, fresh.error)


async def test_enqueue_worker_mode_creates_job_and_worker_runs_it(
    sm: async_sessionmaker[AsyncSession],
    center: Center,
    media_file: Path,
    stub_hooks: dict[str, list],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "get_settings", lambda: get_settings().model_copy(update={"pipeline_mode": "worker"}))
    lesson = await _make_lesson(sm, center, media_file)
    async with sm() as session:
        await enqueue_lesson_processing(session, lesson.id)
        await session.commit()
        job = (await session.execute(select(Job).where(Job.payload["lesson_id"].astext == str(lesson.id)))).scalar_one()
        assert job.kind == "process_lesson"
    try:
        assert await run_once(worker_id="test-worker", sessionmaker=sm) is True
        async with sm() as session:
            fresh_job = await session.get(Job, job.id)
            fresh = await session.get(Lesson, lesson.id)
            assert fresh is not None and fresh.status == LessonStatus.done, (fresh.status_message, fresh.error)
            assert fresh_job is not None and fresh_job.status.value == "done"
    finally:
        async with sm() as session:
            await session.execute(delete(Job).where(Job.id == job.id))
            await session.commit()
