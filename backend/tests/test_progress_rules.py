"""Module B2: skill_states merge rules (mastery cap, reprocess idempotency) and the progress summary."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LessonStatus, SkillState, SkillStatus
from app.pipeline.progress import (
    MAX_EVIDENCE,
    collect_observations,
    resolve_status,
    student_progress_summary,
    trim_evidence,
    update_progress,
)
from tests.test_analysis_support import (
    analysis_with_skill,
    cleanup,
    create_analysis_row,
    create_graph,
    create_lesson,
    ensure_schema,
    fixture_analysis,
    later,
    make_engine,
    sessionmaker_for,
)

SKILL = ("padezhnye_okonchaniya", "Падежные окончания имён существительных")


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


# ---------------------------------------------------------------- pure rules


def test_resolve_status_mastery_cap() -> None:
    assert resolve_status("mastered", ["l1"], set()) == SkillStatus.developing
    assert resolve_status("mastered", ["l1", "l2"], set()) == SkillStatus.mastered
    assert resolve_status("mastered", ["l1", "l2", "l3"], {"l2"}) == SkillStatus.developing
    assert resolve_status("mastered", ["l1", "l2", "l3"], {"l1"}) == SkillStatus.mastered
    assert resolve_status("struggling", ["l1", "l2"], set()) == SkillStatus.struggling
    assert resolve_status("developing", [], set()) == SkillStatus.developing


def test_trim_evidence_keeps_summaries_last() -> None:
    items = [{"kind": "summary", "lesson_id": str(i)} for i in range(15)] + [{"kind": "evidence"}] * 15
    kept = trim_evidence(items)
    assert len(kept) == MAX_EVIDENCE
    assert sum(1 for e in kept if e["kind"] == "summary") == 15


def test_collect_observations_merges_errors_and_summary() -> None:
    from app.db.models import Lesson

    lesson = Lesson(id=uuid.uuid4(), scheduled_at=datetime(2026, 10, 11, tzinfo=UTC))
    obs = collect_observations(fixture_analysis(), lesson)
    assert set(obs) == {"padezhnye_okonchaniya", "tsya_tsa"}
    tsya = obs["tsya_tsa"]
    assert tsya.status == "struggling"
    assert tsya.entries[0]["kind"] == "summary" and tsya.entries[0]["lesson_id"] == str(lesson.id)
    assert any(e["kind"] == "error" for e in tsya.entries)
    assert all(e["lesson_at"] for e in tsya.entries)


# ---------------------------------------------------------------- DB


async def _state(session: AsyncSession, student_id: uuid.UUID, key: str) -> SkillState:
    return (
        await session.execute(
            select(SkillState).where(SkillState.student_id == student_id, SkillState.skill_key == key)
        )
    ).scalar_one()


async def test_mastered_requires_two_clean_lessons_and_reprocess_is_idempotent(session: AsyncSession) -> None:
    g = await create_graph(session)
    center, teacher, student, lesson1 = g["center"], g["teacher"], g["student"], g["lesson"]
    try:
        key, name = SKILL
        a1 = await create_analysis_row(session, lesson1, analysis_with_skill(key, name, "mastered"))
        states = await update_progress(session, lesson1, a1)
        await session.commit()
        assert [s.skill_key for s in states] == [key]
        st = await _state(session, student.id, key)
        assert st.status == SkillStatus.developing  # cap: seen in only one lesson
        assert st.times_seen == 1 and st.times_struggled == 0
        assert st.first_seen_lesson_id == lesson1.id == st.last_seen_lesson_id

        lesson2 = await create_lesson(session, center, teacher, student, later(lesson1.scheduled_at, 7))
        a2 = await create_analysis_row(session, lesson2, analysis_with_skill(key, name, "mastered"))
        await update_progress(session, lesson2, a2)
        await session.commit()
        st = await _state(session, student.id, key)
        assert st.status == SkillStatus.mastered and st.times_seen == 2 and st.times_struggled == 0
        assert st.first_seen_lesson_id == lesson1.id and st.last_seen_lesson_id == lesson2.id
        evidence_before = list(st.evidence)

        # reprocessing lesson 2 must not double count
        await update_progress(session, lesson2, a2)
        await session.commit()
        st = await _state(session, student.id, key)
        assert st.times_seen == 2 and st.times_struggled == 0 and st.status == SkillStatus.mastered
        assert len(st.evidence) == len(evidence_before)
        assert sum(1 for e in st.evidence if e["kind"] == "summary" and e["lesson_id"] == str(lesson2.id)) == 1

        # reprocessing lesson 1 with a changed verdict (struggling) is reflected and keeps chronology
        a1.data = analysis_with_skill(key, name, "struggling", with_error=True).model_dump(mode="json")
        await session.flush()
        await update_progress(session, lesson1, a1)
        await session.commit()
        st = await _state(session, student.id, key)
        assert st.times_seen == 2 and st.times_struggled == 1
        assert st.last_seen_lesson_id == lesson2.id  # chronological, not "last processed"
        # the latest analysis status applied is lesson1's (struggling) — that is what the merge was given
        assert st.status == SkillStatus.struggling
    finally:
        await cleanup(session, center.id)


async def test_struggling_blocks_mastery_and_errors_count(session: AsyncSession) -> None:
    g = await create_graph(session)
    center, teacher, student, lesson1 = g["center"], g["teacher"], g["student"], g["lesson"]
    try:
        key, name = "tsya_tsa", "Правописание -тся / -ться"
        a1 = await create_analysis_row(session, lesson1, analysis_with_skill(key, name, "developing", with_error=True))
        await update_progress(session, lesson1, a1)
        await session.commit()
        st = await _state(session, student.id, key)
        assert st.status == SkillStatus.developing and st.times_struggled == 1  # error present → struggled

        lesson2 = await create_lesson(session, center, teacher, student, later(lesson1.scheduled_at, 7))
        a2 = await create_analysis_row(session, lesson2, analysis_with_skill(key, name, "mastered"))
        await update_progress(session, lesson2, a2)
        await session.commit()
        st = await _state(session, student.id, key)
        assert st.status == SkillStatus.developing  # one of the latest two lessons had struggling

        lesson3 = await create_lesson(session, center, teacher, student, later(lesson1.scheduled_at, 14))
        a3 = await create_analysis_row(session, lesson3, analysis_with_skill(key, name, "mastered"))
        await update_progress(session, lesson3, a3)
        await session.commit()
        st = await _state(session, student.id, key)
        assert st.status == SkillStatus.mastered and st.times_seen == 3 and st.times_struggled == 1

        # a skill that appears only in `errors` becomes struggling with the vocabulary name
        a_err = fixture_analysis().model_copy(update={"skills": []})
        lesson4 = await create_lesson(session, center, teacher, student, later(lesson1.scheduled_at, 21))
        a4 = await create_analysis_row(session, lesson4, a_err)
        touched = await update_progress(session, lesson4, a4)
        await session.commit()
        assert [s.skill_key for s in touched] == ["tsya_tsa"]
        st = await _state(session, student.id, key)
        assert st.status == SkillStatus.struggling and st.times_struggled == 2 and st.times_seen == 4
    finally:
        await cleanup(session, center.id)


async def test_evidence_capped(session: AsyncSession) -> None:
    g = await create_graph(session)
    center, student, lesson1 = g["center"], g["student"], g["lesson"]
    try:
        key, name = SKILL
        a = analysis_with_skill(key, name, "developing")
        many = a.model_copy(update={"skills": [a.skills[0].model_copy(update={"evidence": a.skills[0].evidence * 30})]})
        row = await create_analysis_row(session, lesson1, many)
        await update_progress(session, lesson1, row)
        await session.commit()
        st = await _state(session, student.id, key)
        assert len(st.evidence) == MAX_EVIDENCE and st.times_seen == 1
    finally:
        await cleanup(session, center.id)


async def test_student_progress_summary(session: AsyncSession) -> None:
    g = await create_graph(session)
    center, teacher, student, lesson1 = g["center"], g["teacher"], g["student"], g["lesson"]
    try:
        # lesson2 is created first but scheduled later: summary must be chronological by scheduled_at
        lesson2 = await create_lesson(session, center, teacher, student, later(lesson1.scheduled_at, 7))
        a2 = await create_analysis_row(session, lesson2, fixture_analysis().model_copy(update={"topic": "Урок 2"}))
        a1 = await create_analysis_row(session, lesson1, fixture_analysis().model_copy(update={"topic": "Урок 1"}))
        await update_progress(session, lesson1, a1)
        await update_progress(session, lesson2, a2)
        lesson1.status = LessonStatus.done
        lesson2.status = LessonStatus.done
        await session.commit()

        summary = await student_progress_summary(session, student.id)
        assert [lesson["topic"] for lesson in summary["lessons"]] == ["Урок 1", "Урок 2"]
        first = summary["lessons"][0]
        assert first["lesson_id"] == str(lesson1.id)
        assert first["errors_count"] == 1 and first["independent_successes_count"] == 1
        assert first["tasks_total"] == 2 and first["tasks_solved_independently"] == 1
        assert summary["counts"] == {"struggling": 1, "developing": 1, "mastered": 0}
        skills = summary["skills"]
        assert [s["skill_key"] for s in skills] == ["tsya_tsa", "padezhnye_okonchaniya"]  # struggling first
        tsya = skills[0]
        assert tsya["times_seen"] == 2 and tsya["times_struggled"] == 2
        assert tsya["first_seen_lesson_id"] == str(lesson1.id) and tsya["last_seen_lesson_id"] == str(lesson2.id)
        assert tsya["evidence"] and all("note" in e for e in tsya["evidence"])

        empty = await student_progress_summary(session, uuid.uuid4())
        assert empty["skills"] == [] and empty["lessons"] == [] and empty["counts"]["struggling"] == 0
    finally:
        await cleanup(session, center.id)
