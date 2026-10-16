"""Subject-aware analysis prompt: base + per-subject addendum, subject list endpoint, skill history per subject."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.db.models import Lesson, SkillState, SkillStatus, Transcript
from app.pipeline.analysis import analyze_lesson, build_prompt, states_for_subject
from app.pipeline.prompts import (
    BASE_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_system_prompt,
    build_user_prompt,
    format_skill_vocabulary,
    subject_addendum,
)
from app.providers.llm.base import LLMResult
from app.providers.llm.mock import fallback_analysis
from app.subjects import SUBJECTS, subject_title
from tests.test_analysis_support import (
    TRANSCRIPT_SEGMENTS,
    cleanup,
    create_graph,
    create_lesson,
    ensure_schema,
    make_engine,
    sessionmaker_for,
)

RU_L2_LINES = (
    "в казахском нет категории рода",
    "падежные окончания и предлоги (в казахском — послелоги и аффиксы)",
    "мектепке — к школе",
    "если ученик написал «учиться», а в речи требовалось «учится»",
    "вместо «дательный падеж» — «как меняются окончания слов: к школе, к маме»",
    "glagoly_dvizheniya",
)


# ---------------------------------------------------------------- pure prompt assembly


def test_base_prompt_is_subject_agnostic() -> None:
    assert "русского языка" not in BASE_SYSTEM_PROMPT
    assert "падеж" not in BASE_SYSTEM_PROMPT
    for needle in (
        "≠",
        "mastered",
        "weak",
        "strong",
        "Не придумывай",
        "frame_ids",
        "parent_report",
        "needs_methodist_review",
    ):
        assert needle in BASE_SYSTEM_PROMPT, needle


def test_every_subject_has_a_complete_addendum() -> None:
    for code in SUBJECTS:
        addendum = subject_addendum(code)
        assert addendum, code
        for section in ("### Доказательство усвоения", "### Кадры", "### Родителю"):
            assert section in addendum, (code, section)


def test_russian_addendum_keeps_ru_l2_specifics() -> None:
    prompt = build_system_prompt("russian")
    assert "## Предмет урока: Русский язык" in prompt
    for line in RU_L2_LINES:
        assert line in prompt, line
    assert "русский как родной" in prompt  # both KZ cases are covered
    assert prompt == SYSTEM_PROMPT  # default subject
    assert build_system_prompt(None) == prompt
    assert build_system_prompt("") == prompt


def test_math_prompt_gets_math_addendum_only() -> None:
    prompt = build_system_prompt("math")
    assert prompt.startswith(BASE_SYSTEM_PROMPT)
    assert "## Предмет урока: Математика" in prompt
    assert "(a+b)² = a² + b²" in prompt
    assert "в казахском нет категории рода" not in prompt
    assert build_system_prompt("  MATH ") == prompt
    assert prompt.rstrip().endswith("Без markdown, без пояснений вне JSON.")


@pytest.mark.parametrize("code", ["astronomy", "../russian", "russian.md"])
def test_unknown_subject_gets_base_only(code: str) -> None:
    prompt = build_system_prompt(code)
    assert "## Предмет урока" not in prompt
    assert prompt.startswith(BASE_SYSTEM_PROMPT)
    assert subject_addendum(code) is None


def test_subject_titles_single_source() -> None:
    assert subject_title("ent_prep") == "Подготовка к ЕНТ"
    assert subject_title(None) == "Русский язык"
    assert subject_title("astronomy") == "astronomy"


def _user_prompt(subject: str, native: str = "kk", vocabulary: dict | None = None) -> str:
    return build_user_prompt(
        subject=subject,
        scheduled_at=None,
        duration_sec=600,
        student_grade=9,
        student_native_language=native,
        language_hints=["ru"],
        transcript_segments=TRANSCRIPT_SEGMENTS,
        transcript_language="ru",
        frames=[],
        frames_total=0,
        prior_skills=[],
        skill_vocabulary=vocabulary,
    )


def test_user_prompt_language_line_depends_on_subject() -> None:
    math = _user_prompt("math", vocabulary={})
    assert "- Предмет: Математика" in math
    assert "- Родной язык ученика: казахский\n" in math
    assert "второй язык" not in math
    assert "Стартового словаря для этого предмета нет" in math
    assert "русский — второй язык" in _user_prompt("russian", "kk")
    assert "русский — родной язык" in _user_prompt("russian", "ru")
    assert "казахский — второй язык" in _user_prompt("kazakh", "ru")


def test_skill_vocabulary_default_still_russian() -> None:
    assert "padezhnye_okonchaniya" in format_skill_vocabulary()
    assert "padezhnye_okonchaniya" not in format_skill_vocabulary({})


def test_build_prompt_uses_lesson_subject() -> None:
    lesson = Lesson(subject="physics", language_hints=["ru"])
    bundle = build_prompt(lesson, None, [], None, [], Settings(llm_provider="mock"))
    assert "## Предмет урока: Физика" in bundle.system
    assert "padezhnye_okonchaniya" not in bundle.user_text
    ru = build_prompt(Lesson(subject="russian"), None, [], None, [], Settings(llm_provider="mock"))
    assert "padezhnye_okonchaniya" in ru.user_text


# ---------------------------------------------------------------- endpoint


async def test_subjects_endpoint_lists_codes_in_order(client: AsyncClient) -> None:
    resp = await client.get("/api/subjects")
    assert resp.status_code == 200
    body = resp.json()
    assert [s["code"] for s in body] == list(SUBJECTS)
    assert body[0] == {"code": "russian", "title": "Русский язык"}
    assert {"code": "ielts", "title": "IELTS"} in body


# ---------------------------------------------------------------- skill history per subject


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


async def test_math_lesson_ignores_russian_skill_history(session: AsyncSession) -> None:
    g = await create_graph(session)
    center_id = g["center"].id
    try:
        ru_lesson = g["lesson"]
        math_lesson = await create_lesson(session, g["center"], g["teacher"], g["student"], ru_lesson.scheduled_at)
        math_lesson.subject = "math"
        session.add(Transcript(lesson_id=math_lesson.id, provider="mock", language="ru", segments=TRANSCRIPT_SEGMENTS))
        ru_state = SkillState(
            student_id=g["student"].id,
            skill_key="tsya_tsa",
            skill_name="Правописание -тся / -ться",
            status=SkillStatus.struggling,
            evidence=[{"lesson_id": str(ru_lesson.id), "note": "ошибка", "status": "struggling"}],
            last_seen_lesson_id=ru_lesson.id,
            times_seen=1,
            times_struggled=1,
        )
        math_state = SkillState(
            student_id=g["student"].id,
            skill_key="drobi_slozhenie",
            skill_name="Сложение дробей",
            status=SkillStatus.developing,
            evidence=[{"lesson_id": str(math_lesson.id), "note": "решил с подсказкой", "status": "developing"}],
            last_seen_lesson_id=math_lesson.id,
            times_seen=1,
        )
        orphan_state = SkillState(
            student_id=g["student"].id, skill_key="bez_istorii", skill_name="Без истории", status=SkillStatus.developing
        )
        session.add_all([ru_state, math_state, orphan_state])
        await session.commit()

        states = [ru_state, math_state, orphan_state]
        assert {s.skill_key for s in await states_for_subject(session, states, "math")} == {
            "drobi_slozhenie",
            "bez_istorii",
        }
        assert {s.skill_key for s in await states_for_subject(session, states, "russian")} == {
            "tsya_tsa",
            "bez_istorii",
        }

        captured: dict[str, str] = {}

        class CapturingProvider:
            name = "capture"

            async def analyze(self, *, system, user_text, images, json_schema) -> LLMResult:
                captured["system"], captured["user"] = system, user_text
                return LLMResult(data=fallback_analysis().model_dump(mode="json"), model="capture")

        lesson = (
            await session.execute(
                select(Lesson)
                .options(selectinload(Lesson.transcript), selectinload(Lesson.frames))
                .where(Lesson.id == math_lesson.id)
            )
        ).scalar_one()
        await analyze_lesson(session, lesson, provider=CapturingProvider(), settings=Settings(llm_provider="mock"))
        await session.commit()
        assert "## Предмет урока: Математика" in captured["system"]
        assert "drobi_slozhenie" in captured["user"]
        assert "tsya_tsa" not in captured["user"]
        assert "padezhnye_okonchaniya" not in captured["user"]
    finally:
        await cleanup(session, center_id)
