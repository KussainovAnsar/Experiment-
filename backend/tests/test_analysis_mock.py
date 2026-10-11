"""Module B2: prompt assembly + ``analyze_lesson`` end-to-end with the mock LLM provider (test DB)."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.db.models import Analysis, Frame, FrameReason, Lesson
from app.pipeline.analysis import AnalysisInputError, analyze_lesson, build_prompt, select_frames
from app.pipeline.prompts import PROMPT_VERSION, SYSTEM_PROMPT, PromptSkillState, build_user_prompt, format_transcript
from app.providers.llm.base import ImageInput, LLMResult, extract_json, validation_errors
from app.providers.llm.mock import MockLLMProvider, fallback_analysis
from app.providers.llm.registry import get_llm_provider
from app.schemas.analysis import LessonAnalysis
from tests.test_analysis_support import (
    TRANSCRIPT_SEGMENTS,
    cleanup,
    create_graph,
    ensure_schema,
    make_engine,
    sessionmaker_for,
)


@pytest_asyncio.fixture(scope="module")
async def engine():
    eng = make_engine()
    await ensure_schema(eng)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncIterator[AsyncSession]:
    async with sessionmaker_for(engine)() as s:
        yield s


# ---------------------------------------------------------------- pure helpers


def test_format_transcript_labels_and_timecodes() -> None:
    text = format_transcript(TRANSCRIPT_SEGMENTS)
    assert text.startswith("[00:00] УЧИТЕЛЬ: Сегодня повторяем")
    assert "[00:04] УЧЕНИК: Жақсы, түсіндім." in text
    assert "[00:27] НЕИЗВЕСТНО: Он учиться" in text


def test_format_transcript_elides_middle() -> None:
    segs = [{"start": i * 10, "speaker": "teacher", "text": f"реплика номер {i} " * 5} for i in range(200)]
    text = format_transcript(segs, max_chars=2000)
    assert "часть транскрипта пропущена" in text
    assert len(text) < 2200


def test_system_prompt_encodes_honesty_rules() -> None:
    for needle in ("≠", "mastered", "weak", "strong", "казах", "Не придумывай", "frame_ids"):
        assert needle in SYSTEM_PROMPT, needle


def test_user_prompt_contains_context_and_prior_skills() -> None:
    text = build_user_prompt(
        subject="russian",
        scheduled_at=None,
        duration_sec=1800,
        student_grade=6,
        student_native_language="kk",
        language_hints=["ru", "kk"],
        transcript_segments=TRANSCRIPT_SEGMENTS,
        transcript_language="ru",
        frames=[(0, 0.0, "interval"), (2, 27.0, "scene")],
        frames_total=10,
        prior_skills=[PromptSkillState("tsya_tsa", "Правописание -тся / -ться", "struggling", 2, 2, ["ошибка"])],
    )
    assert "Класс ученика: 6" in text
    assert "Родной язык ученика: казахский" in text
    assert "tsya_tsa (Правописание -тся / -ться): статус «трудности», уроков: 2" in text
    assert "Кадр #2 — 00:27 (смена сцены)" in text
    assert "Всего извлечено кадров: 10; к запросу приложены 2" in text
    assert "padezhnye_okonchaniya — Падежные окончания" in text
    assert PROMPT_VERSION in text


def test_select_frames_thins_evenly_and_skips_missing(tmp_path: Path) -> None:
    frames = []
    for i in range(10):
        p = tmp_path / f"{i:04d}.jpg"
        if i != 4:
            p.write_bytes(b"\xff\xd8\xff\xd9")
        frames.append(
            Frame(lesson_id=uuid.uuid4(), index=i, timestamp_sec=i * 30.0, path=str(p), reason=FrameReason.interval)
        )
    chosen = select_frames(frames, 4)
    assert [f.index for f in chosen] == [0, 3, 6, 9]
    assert 4 not in [f.index for f in select_frames(frames, 100)]
    assert select_frames(frames, 0) == []


def test_extract_json_tolerates_fences_and_prose() -> None:
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Вот ответ: {"a": {"b": 2}} спасибо') == {"a": {"b": 2}}


def test_validation_errors_reports_paths() -> None:
    bad = fallback_analysis().model_dump(mode="json")
    bad["skills"][0]["status"] = "perfect"
    errors = validation_errors(bad)
    assert errors is not None and "skills.0.status" in errors
    assert validation_errors(fallback_analysis().model_dump(mode="json")) is None


async def test_mock_provider_uses_fixture_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = fallback_analysis().model_copy(update={"topic": "Тема из файла"})
    path = tmp_path / "analysis.json"
    path.write_text(custom.model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("MOCK_ANALYSIS_PATH", str(path))
    result = await MockLLMProvider().analyze(system="s", user_text="u", images=[], json_schema={})
    assert result.model == "mock" and result.data["topic"] == "Тема из файла"
    monkeypatch.setenv("MOCK_ANALYSIS_PATH", str(tmp_path / "missing.json"))
    result = await MockLLMProvider().analyze(system="s", user_text="u", images=[], json_schema={})
    assert result.data["topic"] == fallback_analysis().topic
    assert isinstance(get_llm_provider(Settings(llm_provider="mock")), MockLLMProvider)


# ---------------------------------------------------------------- end-to-end against the DB


async def test_analyze_lesson_with_mock_provider(session: AsyncSession, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MOCK_ANALYSIS_PATH", str(tmp_path / "no-such-file.json"))
    g = await create_graph(session, frames_dir=tmp_path)
    await session.commit()
    try:
        lesson = (
            await session.execute(
                select(Lesson)
                .options(selectinload(Lesson.transcript), selectinload(Lesson.frames))
                .where(Lesson.id == g["lesson"].id)
            )
        ).scalar_one()
        settings = Settings(llm_provider="mock", llm_max_frames=2)
        bundle = build_prompt(lesson, lesson.transcript, list(lesson.frames), g["student"], [], settings)
        assert bundle.frames_used == [0, 2]  # 4 frames, one without file, thinned to 2
        assert all(isinstance(i, ImageInput) and i.label.startswith("Кадр #") for i in bundle.images)
        assert "[00:13] УЧЕНИК: Я иду к школа... к школе." in bundle.user_text
        assert bundle.json_schema["title"] == "LessonAnalysis"

        row = await analyze_lesson(session, lesson, settings=settings)
        await session.commit()
        assert isinstance(row, Analysis)
        assert row.provider == "mock" and row.model == "mock" and row.prompt_version == "1.0"
        assert row.latency_ms is not None and row.tokens_in is not None
        validated = LessonAnalysis.model_validate(row.data)
        assert validated.topic == fallback_analysis().topic

        # second run upserts the same row (unique lesson_id) instead of inserting another one
        row2 = await analyze_lesson(session, lesson, settings=settings)
        await session.commit()
        assert row2.id == row.id
        count = (await session.execute(select(Analysis).where(Analysis.lesson_id == lesson.id))).scalars().all()
        assert len(count) == 1
    finally:
        await cleanup(session, g["center"].id)


async def test_analyze_lesson_rejects_invalid_provider_output(session: AsyncSession) -> None:
    g = await create_graph(session)
    await session.commit()
    center_id = g["center"].id
    try:
        lesson = (
            await session.execute(
                select(Lesson)
                .options(selectinload(Lesson.transcript), selectinload(Lesson.frames))
                .where(Lesson.id == g["lesson"].id)
            )
        ).scalar_one()

        class BrokenProvider:
            name = "broken"

            async def analyze(self, *, system, user_text, images, json_schema) -> LLMResult:
                return LLMResult(data={"topic": "x"}, model="broken")

        lesson_id = lesson.id
        with pytest.raises(ValidationError):
            await analyze_lesson(session, lesson, provider=BrokenProvider(), settings=Settings(llm_provider="mock"))
        await session.rollback()
        rows = (await session.execute(select(Analysis).where(Analysis.lesson_id == lesson_id))).scalars().all()
        assert rows == []
    finally:
        await cleanup(session, center_id)


async def test_analyze_lesson_requires_transcript(session: AsyncSession) -> None:
    g = await create_graph(session, with_transcript=False)
    await session.commit()
    try:
        lesson = (
            await session.execute(
                select(Lesson)
                .options(selectinload(Lesson.transcript), selectinload(Lesson.frames))
                .where(Lesson.id == g["lesson"].id)
            )
        ).scalar_one()
        with pytest.raises(AnalysisInputError):
            await analyze_lesson(session, lesson, provider=MockLLMProvider(), settings=Settings(llm_provider="mock"))
    finally:
        await cleanup(session, g["center"].id)


def test_fixture_analysis_roundtrips_json() -> None:
    data = json.loads(fallback_analysis().model_dump_json())
    assert LessonAnalysis.model_validate(data).schema_version == "1.0"
