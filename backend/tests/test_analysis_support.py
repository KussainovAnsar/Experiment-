"""Shared helpers for module B2 tests (analysis / reports / progress).

These tests do not rely on ``conftest.py`` fixtures: they open their own engine from ``DATABASE_URL``
(default: the ``lessonlens_test`` database), create the schema with ``create_all(checkfirst=True)`` and delete
the rows they created. The module name matches ``test_analysis_*`` so it lives in module B2's owned paths;
it defines no tests.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

from PIL import Image
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.base import Base
from app.db.models import (
    Analysis,
    Center,
    Frame,
    FrameReason,
    Lesson,
    LessonStatus,
    Report,
    SkillState,
    Student,
    Teacher,
    Transcript,
)
from app.providers.llm.mock import fallback_analysis
from app.schemas.analysis import Evidence, LessonAnalysis, SkillAssessment, StudentError

TEST_DB_URL = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://lessonlens:lessonlens@127.0.0.1:5432/lessonlens_test"
)

TRANSCRIPT_SEGMENTS = [
    {
        "start": 0.0,
        "end": 4.5,
        "speaker": "teacher",
        "text": "Сегодня повторяем падежные окончания.",
        "confidence": 0.95,
    },
    {"start": 4.5, "end": 7.0, "speaker": "student", "text": "Жақсы, түсіндім.", "confidence": 0.9},
    {
        "start": 7.0,
        "end": 13.0,
        "speaker": "teacher",
        "text": "Я иду к ... (школа). Какое окончание?",
        "confidence": 0.9,
    },
    {"start": 13.0, "end": 16.0, "speaker": "student", "text": "Я иду к школа... к школе.", "confidence": 0.8},
    {
        "start": 22.0,
        "end": 27.0,
        "speaker": "teacher",
        "text": "Напиши: «Он учится в пятом классе».",
        "confidence": 0.9,
    },
    {"start": 27.0, "end": 31.0, "speaker": "unknown", "text": "Он учиться в пятом классе.", "confidence": 0.7},
]


def make_engine() -> AsyncEngine:
    """Own engine for the test module (NullPool: no cross-loop connection reuse)."""
    return create_async_engine(TEST_DB_URL, poolclass=NullPool)


async def ensure_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)


def sessionmaker_for(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def write_jpeg(path, text_color=(20, 20, 20)) -> None:
    """A tiny valid JPEG so frame files exist on disk."""
    img = Image.new("RGB", (64, 48), color=(240, 240, 240))
    for x in range(10, 50):
        img.putpixel((x, 24), text_color)
    img.save(path, format="JPEG")


async def create_graph(session: AsyncSession, *, frames_dir=None, with_transcript: bool = True) -> dict:
    """Create centre → teacher → student → lesson (+ transcript, frames); returns the rows."""
    center = Center(name="Тестовый центр B2", city="Алматы")
    session.add(center)
    await session.flush()
    teacher = Teacher(center_id=center.id, full_name="Айгуль Сериковна", subjects=["russian"])
    student = Student(center_id=center.id, full_name="Айдана Сериккызы", grade=5)
    session.add_all([teacher, student])
    await session.flush()
    lesson = await create_lesson(session, center, teacher, student, datetime(2026, 10, 11, 9, 0, tzinfo=UTC))
    transcript = None
    if with_transcript:
        transcript = Transcript(
            lesson_id=lesson.id,
            provider="mock",
            language="ru",
            segments=TRANSCRIPT_SEGMENTS,
            full_text=" ".join(s["text"] for s in TRANSCRIPT_SEGMENTS),
            speaker_map={"SPEAKER_00": "teacher", "SPEAKER_01": "student"},
            word_count=30,
            duration_sec=31.0,
        )
        session.add(transcript)
    frames: list[Frame] = []
    if frames_dir is not None:
        for idx, ts in enumerate([0.0, 15.0, 27.0, 40.0]):
            path = frames_dir / f"{idx:04d}.jpg"
            if idx != 3:  # frame 3 deliberately has no file on disk
                write_jpeg(path)
            frames.append(
                Frame(
                    lesson_id=lesson.id,
                    index=idx,
                    timestamp_sec=ts,
                    path=str(path),
                    width=64,
                    height=48,
                    reason=FrameReason.scene if idx == 2 else FrameReason.interval,
                    selected_for_llm=True,
                )
            )
        session.add_all(frames)
    await session.flush()
    return {
        "center": center,
        "teacher": teacher,
        "student": student,
        "lesson": lesson,
        "transcript": transcript,
        "frames": frames,
    }


async def create_lesson(
    session: AsyncSession, center: Center, teacher: Teacher, student: Student, at: datetime
) -> Lesson:
    lesson = Lesson(
        center_id=center.id,
        teacher_id=teacher.id,
        student_id=student.id,
        subject="russian",
        scheduled_at=at,
        duration_sec=41.0,
        status=LessonStatus.analyzing,
        consent_confirmed=True,
        language_hints=["ru", "kk"],
    )
    session.add(lesson)
    await session.flush()
    return lesson


async def create_analysis_row(session: AsyncSession, lesson: Lesson, analysis: LessonAnalysis) -> Analysis:
    row = Analysis(lesson_id=lesson.id, provider="mock", model="mock", data=analysis.model_dump(mode="json"))
    session.add(row)
    await session.flush()
    return row


async def cleanup(session: AsyncSession, center_id: uuid.UUID) -> None:
    """Delete everything created under ``center_id`` (cascades handle the rest)."""
    await session.execute(delete(Center).where(Center.id == center_id))
    await session.commit()


# ---------------------------------------------------------------- analysis fixtures (Russian lesson)


def fixture_analysis() -> LessonAnalysis:
    """Realistic analysis: падежные окончания (developing) + -тся/-ться (struggling, one error)."""
    return fallback_analysis()


def analysis_with_skill(skill_key: str, skill_name: str, status: str, *, with_error: bool = False) -> LessonAnalysis:
    """Fixture analysis whose ``skills`` contain only ``skill_key`` with ``status`` (optionally one error)."""
    base = fallback_analysis()
    skills = [
        SkillAssessment(
            skill_key=skill_key,
            skill_name=skill_name,
            status=status,  # type: ignore[arg-type]
            evidence=[Evidence(timestamp=13.0, description=f"Наблюдение по навыку {skill_name}")],
        )
    ]
    errors = (
        [
            StudentError(
                timestamp=27.0,
                skill_key=skill_key,
                skill_name=skill_name,
                description=f"Ошибка по навыку {skill_name}",
                corrected=False,
            )
        ]
        if with_error
        else []
    )
    return base.model_copy(update={"skills": skills, "errors": errors, "misconceptions": []})


def later(at: datetime, days: int) -> datetime:
    return at + timedelta(days=days)


__all__ = [
    "TRANSCRIPT_SEGMENTS",
    "Report",
    "SkillState",
    "analysis_with_skill",
    "cleanup",
    "create_analysis_row",
    "create_graph",
    "create_lesson",
    "ensure_schema",
    "fixture_analysis",
    "later",
    "make_engine",
    "sessionmaker_for",
    "write_jpeg",
]
