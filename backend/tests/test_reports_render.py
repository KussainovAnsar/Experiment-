"""Module B2: report rendering (pure) and ``build_reports`` versioning against the test DB."""

from __future__ import annotations

import html
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Report, ReportKind
from app.pipeline.reports import (
    TELEGRAM_MAX_CHARS,
    ReportContext,
    build_episodes,
    build_manager_report_data,
    build_parent_report_data,
    build_report_payloads,
    build_reports,
    build_teacher_report_data,
    first_name,
    fmt_ts,
    format_date_ru,
    render_manager_markdown,
    render_parent_markdown,
    render_parent_report_telegram_html,
    render_teacher_markdown,
)
from app.schemas.analysis import StudentError, TeachingEvent
from tests.test_analysis_support import (
    cleanup,
    create_analysis_row,
    create_graph,
    ensure_schema,
    fixture_analysis,
    make_engine,
    sessionmaker_for,
)

_ALLOWED_TAGS = {"b", "i", "u", "s", "a", "code"}
_TAG_RE = re.compile(r"</?([a-zA-Z0-9]+)[^>]*>")


def _ctx(**overrides) -> ReportContext:
    base = dict(
        lesson_id=uuid.uuid4(),
        student_id=uuid.uuid4(),
        scheduled_at=datetime(2026, 10, 11, 4, 30, tzinfo=UTC),  # 09:30 in Asia/Almaty
        student_first_name="Айдана",
        teacher_name="Айгуль Сериковна",
        frames_by_index={1: {"frame_id": str(uuid.uuid4()), "timestamp_sec": 27.0}},
    )
    base.update(overrides)
    return ReportContext(**base)


# ---------------------------------------------------------------- helpers


def test_helpers() -> None:
    assert fmt_ts(0) == "00:00" and fmt_ts(65.4) == "01:05" and fmt_ts(3725) == "1:02:05"
    assert format_date_ru(datetime(2026, 10, 11, 20, 30, tzinfo=UTC)) == "12 октября 2026"  # crosses midnight in Almaty
    assert format_date_ru(None) == ""
    assert first_name("Айдана Сериккызы") == "Айдана" and first_name("  ") is None


# ---------------------------------------------------------------- teacher


def test_teacher_episodes_chain() -> None:
    a = fixture_analysis()
    ctx = _ctx()
    episodes = build_episodes(a, ctx)
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep["timecode"] == "00:27" and ep["skill_key"] == "tsya_tsa"
    assert ep["re_explanation"]["timecode"] == "00:31"
    assert ep["repeated"] is False and ep["corrected"] is True
    assert ep["frames"][0]["index"] == 1 and ep["frames"][0]["frame_id"] == ctx.frames_by_index[1]["frame_id"]

    # a second error on the same skill after the re-explanation marks the episode as repeated
    a2 = a.model_copy(
        update={
            "errors": [
                *a.errors,
                StudentError(
                    timestamp=120.0, skill_key="tsya_tsa", skill_name="-тся/-ться", description="Снова «смеяться»"
                ),
            ]
        }
    )
    eps = build_episodes(a2, ctx)
    assert eps[0]["repeated"] is True and eps[0]["repeated_at"] == "02:00"
    assert eps[1]["re_explanation"] is None


def test_teacher_report_data_and_markdown() -> None:
    a = fixture_analysis()
    data = build_teacher_report_data(a, _ctx())
    assert data["kind"] == "teacher" and data["date"] == "11 октября 2026"
    assert [r["priority"] for r in data["recommendations"]] == ["high", "medium"]
    assert data["visual_issues"][0]["issue"] and data["visual_issues"][0]["frame"]["index"] == 1
    assert data["misconceptions"][0]["skill_name"] == "Правописание -тся / -ться"
    md = render_teacher_markdown(data)
    for section in ("## Эпизоды с ошибками", "## Рекомендации", "## Замечено на экране", "[00:27]", "[00:31]"):
        assert section in md
    assert "переобъяснение в [00:31]" in md


def test_frame_references_are_tolerant() -> None:
    a = fixture_analysis()
    ctx = _ctx(frames_by_index={})  # no frames at all
    data = build_teacher_report_data(a, ctx)
    assert data["episodes"][0]["frames"] == []
    assert data["visual_issues"][0]["frame"] is None
    render_teacher_markdown(data)
    a2 = a.model_copy(
        update={
            "timeline": [TeachingEvent(start=1, end=2, kind="task", speaker="teacher", summary="x", frame_ids=[999])]
        }
    )
    assert build_teacher_report_data(a2, _ctx())["timeline"][0]["frames"] == []


# ---------------------------------------------------------------- manager


def test_manager_report_has_indicators_and_no_rating() -> None:
    a = fixture_analysis()
    data = build_manager_report_data(a, _ctx())
    assert data["indicators"] == {
        "objectives_checked": True,
        "difficulties_detected": True,
        "re_explanation_happened": True,
        "needs_methodist_review": False,
    }
    assert data["needs_review"] is False and data["review_reason"] is None
    assert data["talk_shares"] == {"teacher": 0.68, "student": 0.32}
    assert data["counts"]["errors"] == 1 and data["counts"]["tasks_total"] == 2
    assert data["counts"]["skills_struggling"] == 1 and data["counts"]["objectives_checked"] == 1
    assert not any(k in data for k in ("rating", "score", "teacher_rating"))
    md = render_manager_markdown(data)
    assert "## Индикаторы качества" in md and "Доли речи: учитель 68%, ученик 32%" in md
    assert "рейтинг" not in md.lower()

    flagged = a.model_copy(
        update={
            "manager_indicators": a.manager_indicators.model_copy(
                update={"needs_methodist_review": True, "review_reason": "Цели не проверены"}
            )
        }
    )
    md2 = render_manager_markdown(build_manager_report_data(flagged, _ctx()))
    assert "Требуется разбор методиста: **ДА** — Цели не проверены" in md2


# ---------------------------------------------------------------- parent


def test_parent_report_data_and_markdown() -> None:
    a = fixture_analysis()
    data = build_parent_report_data(a, _ctx())
    assert data["child_first_name"] == "Айдана" and data["date"] == "11 октября 2026"
    assert data["topic"] == a.topic and data["topic_plain"] == a.parent_report.topic_plain
    assert data["skills_snapshot"] == {
        "struggling": ["Правописание -тся / -ться"],
        "developing": ["Падежные окончания имён существительных"],
        "mastered": [],
    }
    md = render_parent_markdown(data)
    for section in ("## Тема урока", "## Что получилось самостоятельно", "## Где были ошибки", "## Что повторить дома"):
        assert section in md
    assert a.parent_report.encouragement in md


def _assert_safe_html(text: str) -> None:
    for tag in _TAG_RE.findall(text):
        assert tag.lower() in _ALLOWED_TAGS, tag
    assert "<ul" not in text and "<li" not in text and "<br" not in text
    assert text.count("<b>") == text.count("</b>") and text.count("<i>") == text.count("</i>")
    assert text.count("<a ") == text.count("</a>")


def test_parent_telegram_html_basic() -> None:
    a = fixture_analysis()
    data = build_parent_report_data(a, _ctx())
    out = render_parent_report_telegram_html(data, "https://app.example.kz/p/abc?x=1&y=2")
    assert len(out) <= TELEGRAM_MAX_CHARS
    _assert_safe_html(out)
    assert out.startswith("<b>Айдана — русский язык, 11 октября 2026</b>")
    for section in ("<b>Получилось самостоятельно</b>", "<b>Где были ошибки</b>", "<b>Что повторить дома</b>", "• "):
        assert section in out
    assert out.rstrip().endswith(
        '<a href="https://app.example.kz/p/abc?x=1&amp;y=2">Полный отчёт — в кабинете родителя</a>'
    )
    assert "…" not in out  # nothing truncated for the short fixture

    without_link = render_parent_report_telegram_html(data, None)
    assert without_link.rstrip().endswith("Полный отчёт — в кабинете родителя") and "<a " not in without_link


def test_parent_telegram_html_escapes_user_text() -> None:
    data = build_parent_report_data(fixture_analysis(), _ctx(student_first_name="<Имя> & Co"))
    data["what_was_hard"] = ['Писал <b>жирно</b> & "кавычки"']
    data["encouragement"] = "<script>alert(1)</script>"
    out = render_parent_report_telegram_html(data, "https://x.kz/p/t")
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert "&lt;b&gt;жирно&lt;/b&gt; &amp;" in out
    assert "<b>&lt;Имя&gt; &amp; Co — русский язык" in out
    _assert_safe_html(out)
    assert html.unescape(out).count("<script>") == 1  # only inside escaped text


def test_parent_telegram_html_truncates_gracefully() -> None:
    data = build_parent_report_data(fixture_analysis(), _ctx())
    data["what_to_repeat"] = [
        f"Пункт {i}: повторить правило и записать несколько примеров в тетрадь" for i in range(40)
    ]
    data["what_was_hard"] = ["Очень длинное описание трудности " * 20]
    url = "https://app.example.kz/p/" + "t" * 60
    out = render_parent_report_telegram_html(data, url)
    assert len(out) <= TELEGRAM_MAX_CHARS
    _assert_safe_html(out)
    assert "…" in out
    assert out.rstrip().endswith(f'<a href="{url}">Полный отчёт — в кабинете родителя</a>')
    assert "<b>Айдана" in out  # header survives

    # one giant single line still fits the limit and keeps the footer
    data2 = build_parent_report_data(fixture_analysis(), _ctx())
    data2["topic_plain"] = "слово " * 1000
    out2 = render_parent_report_telegram_html(data2, None)
    assert len(out2) <= TELEGRAM_MAX_CHARS and out2.rstrip().endswith("Полный отчёт — в кабинете родителя")


def test_build_report_payloads_shapes() -> None:
    payloads = build_report_payloads(fixture_analysis(), _ctx())
    assert set(payloads) == {ReportKind.teacher, ReportKind.manager, ReportKind.parent}
    assert payloads[ReportKind.teacher][2] is None and payloads[ReportKind.parent][2]


# ---------------------------------------------------------------- DB: versioning


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


async def test_build_reports_creates_versions(session: AsyncSession, tmp_path) -> None:
    g = await create_graph(session, frames_dir=tmp_path)
    analysis = await create_analysis_row(session, g["lesson"], fixture_analysis())
    await session.commit()
    try:
        first = await build_reports(session, g["lesson"], analysis)
        await session.commit()
        assert sorted(r.kind.value for r in first) == ["manager", "parent", "teacher"]
        assert all(r.version == 1 and r.language.value == "ru" and r.published_at is not None for r in first)
        parent = next(r for r in first if r.kind == ReportKind.parent)
        assert parent.telegram_html and len(parent.telegram_html) <= TELEGRAM_MAX_CHARS
        assert parent.data["child_first_name"] == "Айдана" and parent.data["date"] == "11 октября 2026"
        teacher = next(r for r in first if r.kind == ReportKind.teacher)
        frame_ref = teacher.data["episodes"][0]["frames"][0]
        assert frame_ref["index"] == 2 or frame_ref["index"] == 1  # whichever frame index 1 maps to
        assert uuid.UUID(frame_ref["frame_id"]) in {f.id for f in g["frames"]}
        assert teacher.data["teacher_name"] == "Айгуль Сериковна"

        second = await build_reports(session, g["lesson"], analysis)
        await session.commit()
        assert all(r.version == 2 for r in second)
        rows = (await session.execute(select(Report).where(Report.lesson_id == g["lesson"].id))).scalars().all()
        assert len(rows) == 6
    finally:
        await cleanup(session, g["center"].id)
