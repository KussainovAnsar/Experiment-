"""One ``LessonAnalysis`` → three reports (teacher / manager / parent), see docs/CONTRACT.md §8.

Each report has audience-specific ``data`` (JSON), Russian ``markdown`` with mm:ss timecodes and — for the
parent — ``telegram_html`` (≤ 1500 chars, aiogram-safe HTML). Frame references are tolerant: a ``frame_id``
that is not among the lesson's frames is simply rendered without a link. Reports are versioned per
``(lesson, kind)``; ``build_reports`` always creates a new version. Nothing is committed here.
"""

from __future__ import annotations

import html
import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Analysis, Center, Frame, Language, Lesson, Report, ReportKind, Student, Teacher
from app.logging import get_logger
from app.pipeline.prompts import format_timestamp, subject_title
from app.schemas.analysis import RUSSIAN_SKILL_KEYS, LessonAnalysis, StudentError, TeachingEvent

log = get_logger("app.pipeline.reports")

TELEGRAM_MAX_CHARS = 1500
PORTAL_FOOTER_TEXT = "Полный отчёт — в кабинете родителя"
DEFAULT_TIMEZONE = "Asia/Almaty"
# A re-explanation this far after an error is still attributed to it.
RE_EXPLANATION_WINDOW_SEC = 900.0

_MONTHS_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]  # fmt: skip
_STATUS_RU = {"struggling": "трудности", "developing": "в развитии", "mastered": "освоено"}
_PRIORITY_RU = {"high": "высокий", "medium": "средний", "low": "низкий"}
_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
_SIGNAL_RU = {"strong": "сильный", "useful": "полезный", "weak": "слабый"}
_KIND_RU = {
    "explanation": "объяснение",
    "task": "задание",
    "student_answer": "ответ ученика",
    "student_error": "ошибка ученика",
    "re_explanation": "переобъяснение",
    "check": "проверка",
    "visual_material": "наглядный материал",
    "off_topic": "не по теме",
    "other": "другое",
}
_SPEAKER_RU = {"teacher": "учитель", "student": "ученик", "both": "оба"}
_RELEVANCE_RU = {"high": "высокая", "medium": "средняя", "low": "низкая"}
_WS_RE = re.compile(r"\s+")


# ---------------------------------------------------------------- context & helpers


@dataclass(slots=True)
class ReportContext:
    """Lesson facts that are not part of the analysis but needed by the reports."""

    lesson_id: uuid.UUID
    student_id: uuid.UUID
    scheduled_at: datetime | None
    subject: str = "russian"
    timezone: str = DEFAULT_TIMEZONE
    student_first_name: str | None = None
    teacher_name: str | None = None
    # frames.index -> {"frame_id": str, "timestamp_sec": float}
    frames_by_index: dict[int, dict] = field(default_factory=dict)


def fmt_ts(seconds: float | None) -> str:
    """Seconds → ``mm:ss`` (``h:mm:ss`` above one hour)."""
    return format_timestamp(seconds)


def first_name(full_name: str | None) -> str | None:
    """First token of a full name (``«Айдана Сериккызы»`` → ``Айдана``); ``None`` when empty."""
    if not full_name:
        return None
    parts = full_name.strip().split()
    return parts[0] if parts else None


def format_date_ru(dt: datetime | None, timezone: str = DEFAULT_TIMEZONE) -> str:
    """``11 октября 2026`` in the centre's timezone; empty string when unknown."""
    if dt is None:
        return ""
    try:
        tz = ZoneInfo(timezone or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    local = dt.astimezone(tz)
    return f"{local.day} {_MONTHS_GEN[local.month - 1]} {local.year}"


def frame_refs(frame_ids: Iterable[int], ctx: ReportContext) -> list[dict]:
    """Resolve ``frame_ids`` → ``[{index, frame_id, timestamp_sec, timecode}]``; unknown indices are skipped."""
    out: list[dict] = []
    for idx in frame_ids or []:
        try:
            key = int(idx)
        except (TypeError, ValueError):
            continue
        meta = ctx.frames_by_index.get(key)
        if meta is None:
            continue
        ts = meta.get("timestamp_sec")
        out.append({"index": key, "frame_id": str(meta.get("frame_id")), "timestamp_sec": ts, "timecode": fmt_ts(ts)})
    return out


def _skill_names(a: LessonAnalysis) -> dict[str, str]:
    names = dict(RUSSIAN_SKILL_KEYS)
    for e in a.errors:
        names.setdefault(e.skill_key, e.skill_name)
    for s in a.skills:
        names[s.skill_key] = s.skill_name
    return names


def _evidence(items: Sequence, ctx: ReportContext) -> list[dict]:
    return [
        {
            "timestamp": ev.timestamp,
            "timecode": fmt_ts(ev.timestamp),
            "description": ev.description,
            "quote": ev.quote,
            "frames": frame_refs(ev.frame_ids, ctx),
        }
        for ev in items
    ]


def _clean(text: str | None) -> str:
    return _WS_RE.sub(" ", str(text or "")).strip()


# ---------------------------------------------------------------- teacher


def _find_re_explanation(
    error: StudentError, errors: Sequence[StudentError], timeline: Sequence[TeachingEvent]
) -> TeachingEvent | None:
    """The re-explanation that answers ``error``, if any.

    ``TeachingEvent`` carries no ``skill_key``, so skill is inferred from order: a ``re_explanation`` within
    :data:`RE_EXPLANATION_WINDOW_SEC` after the error is attributed to it only when no error of a *different*
    skill happened in between — otherwise the teacher was re-explaining that later error.
    """
    candidates = sorted(
        (
            ev
            for ev in timeline
            if ev.kind == "re_explanation"
            and error.timestamp <= ev.start <= error.timestamp + RE_EXPLANATION_WINDOW_SEC
        ),
        key=lambda ev: ev.start,
    )
    for ev in candidates:
        intervening = any(
            error.timestamp < other.timestamp <= ev.start and other.skill_key != error.skill_key for other in errors
        )
        if not intervening:
            return ev
    return None


def build_episodes(a: LessonAnalysis, ctx: ReportContext) -> list[dict]:
    """Chronological error episodes: error → re-explanation? → repeated after it? → corrected?

    ``repeated_at`` is the next error of the same skill **after** the attributed re-explanation; without a
    re-explanation no repeat is claimed (``repeated`` then only reflects the LLM's ``repeated_after_explanation``).
    """
    errors = sorted(a.errors, key=lambda e: e.timestamp)
    episodes: list[dict] = []
    for i, err in enumerate(errors):
        later_same = [e for e in errors[i + 1 :] if e.skill_key == err.skill_key]
        re_expl = _find_re_explanation(err, errors, a.timeline)
        repeated_at: float | None = None
        if re_expl is not None:
            after = [e for e in later_same if e.timestamp > re_expl.start]
            repeated_at = after[0].timestamp if after else None
        repeated = bool(err.repeated_after_explanation) or repeated_at is not None
        episodes.append(
            {
                "timestamp": err.timestamp,
                "timecode": fmt_ts(err.timestamp),
                "skill_key": err.skill_key,
                "skill_name": err.skill_name,
                "description": err.description,
                "quote": err.quote,
                "frames": frame_refs(err.frame_ids, ctx),
                "re_explanation": (
                    {
                        "timestamp": re_expl.start,
                        "timecode": fmt_ts(re_expl.start),
                        "summary": re_expl.summary,
                        "quote": re_expl.quote,
                    }
                    if re_expl is not None
                    else None
                ),
                "repeated": repeated,
                "repeated_after_explanation": err.repeated_after_explanation,
                "repeated_at": fmt_ts(repeated_at) if repeated_at is not None else None,
                "corrected": err.corrected,
            }
        )
    return episodes


def build_teacher_report_data(a: LessonAnalysis, ctx: ReportContext) -> dict:
    """Audience: the teacher — concrete episodes with timecodes, recommendations, what was seen on screen."""
    names = _skill_names(a)
    recommendations = sorted(
        (
            {
                "timestamp": r.timestamp,
                "timecode": fmt_ts(r.timestamp) if r.timestamp is not None else None,
                "observation": r.observation,
                "suggestion": r.suggestion,
                "priority": r.priority,
                "priority_ru": _PRIORITY_RU[r.priority],
            }
            for r in a.teacher_recommendations
        ),
        key=lambda r: (_PRIORITY_ORDER[r["priority"]], r["timestamp"] if r["timestamp"] is not None else 1e12),
    )
    visual_issues = [
        {
            "frame_id": v.frame_id,
            "frame": (frame_refs([v.frame_id], ctx) or [None])[0],
            "timestamp": v.timestamp,
            "timecode": fmt_ts(v.timestamp),
            "description": v.description,
            "text_detected": v.text_detected,
            "issue": v.issue,
            "relevance": v.relevance,
        }
        for v in sorted(a.visual_observations, key=lambda v: v.timestamp)
        if v.issue or v.relevance == "high"
    ]
    return {
        "kind": "teacher",
        "lesson_id": str(ctx.lesson_id),
        "student_id": str(ctx.student_id),
        "subject": a.subject or subject_title(ctx.subject),
        "topic": a.topic,
        "date": format_date_ru(ctx.scheduled_at, ctx.timezone),
        "lesson_date_iso": ctx.scheduled_at.isoformat() if ctx.scheduled_at else None,
        "student_first_name": ctx.student_first_name,
        "teacher_name": ctx.teacher_name,
        "lesson_summary": a.lesson_summary,
        "learning_objectives": [o.model_dump() for o in a.learning_objectives],
        "episodes": build_episodes(a, ctx),
        "independent_successes": _evidence(a.independent_successes, ctx),
        "recommendations": recommendations,
        "visual_issues": visual_issues,
        "misconceptions": [
            {
                "skill_key": m.skill_key,
                "skill_name": names.get(m.skill_key, m.skill_key),
                "description": m.description,
                "confidence": m.confidence,
                "signal_strength": m.signal_strength,
                "signal_strength_ru": _SIGNAL_RU[m.signal_strength],
                "evidence": _evidence(m.evidence, ctx),
            }
            for m in sorted(a.misconceptions, key=lambda m: -m.confidence)
        ],
        "skills": [
            {
                "skill_key": s.skill_key,
                "skill_name": s.skill_name,
                "status": s.status,
                "status_ru": _STATUS_RU[s.status],
                "evidence": _evidence(s.evidence, ctx),
            }
            for s in a.skills
        ],
        "timeline": [
            {
                "start": ev.start,
                "end": ev.end,
                "timecode": fmt_ts(ev.start),
                "kind": ev.kind,
                "kind_ru": _KIND_RU[ev.kind],
                "speaker": ev.speaker,
                "speaker_ru": _SPEAKER_RU[ev.speaker],
                "summary": ev.summary,
                "quote": ev.quote,
                "frames": frame_refs(ev.frame_ids, ctx),
            }
            for ev in sorted(a.timeline, key=lambda ev: ev.start)
        ],
        "limitations": list(a.limitations),
        "language_notes": a.language_notes,
    }


def render_teacher_markdown(data: dict) -> str:
    """Russian markdown of the teacher report."""
    md: list[str] = [f"# Отчёт для преподавателя — {data['topic']}"]
    meta = [f"**Дата:** {data['date']}" if data.get("date") else None]
    if data.get("student_first_name"):
        meta.append(f"**Ученик:** {data['student_first_name']}")
    md.append(" · ".join(m for m in meta if m))
    md += ["", "## Кратко", data["lesson_summary"]]
    if data["learning_objectives"]:
        md += ["", "## Цели урока"]
        for o in data["learning_objectives"]:
            mark = "✅ проверена" if o["was_checked"] else "⚠️ не проверена"
            md.append(f"- {o['text']} — {mark}" + (f" ({o['evidence']})" if o.get("evidence") else ""))
    md += ["", "## Эпизоды с ошибками"]
    if not data["episodes"]:
        md.append("Ошибок ученика в записи не зафиксировано.")
    for ep in data["episodes"]:
        md.append(f"### [{ep['timecode']}] {ep['skill_name']}")
        md.append(ep["description"])
        if ep.get("quote"):
            md.append(f"> {ep['quote']}")
        chain = []
        if ep["re_explanation"]:
            chain.append(f"переобъяснение в [{ep['re_explanation']['timecode']}]: {ep['re_explanation']['summary']}")
        else:
            chain.append("переобъяснения не было")
        chain.append(
            f"ошибка повторилась в [{ep['repeated_at']}]"
            if ep["repeated"] and ep.get("repeated_at")
            else ("ошибка повторилась после объяснения" if ep["repeated"] else "повтора не зафиксировано")
        )
        chain.append("исправлена в ходе урока" if ep["corrected"] else "не исправлена")
        md.append("- " + "; ".join(chain))
        if ep["frames"]:
            md.append("- Кадры: " + ", ".join(f"#{f['index']} ({f['timecode']})" for f in ep["frames"]))
    if data["independent_successes"]:
        md += ["", "## Самостоятельные успехи"]
        for s in data["independent_successes"]:
            md.append(f"- [{s['timecode']}] {s['description']}")
    md += ["", "## Рекомендации"]
    if not data["recommendations"]:
        md.append("Рекомендаций нет.")
    for r in data["recommendations"]:
        tc = f"[{r['timecode']}] " if r.get("timecode") else ""
        md.append(f"- **{r['priority_ru']}** {tc}{r['observation']}  \n  → {r['suggestion']}")
    if data["visual_issues"]:
        md += ["", "## Замечено на экране"]
        for v in data["visual_issues"]:
            line = f"- [{v['timecode']}] кадр #{v['frame_id']}: {v['description']}"
            if v.get("text_detected"):
                line += f" — текст: «{v['text_detected']}»"
            if v.get("issue"):
                line += f" — **{v['issue']}**"
            md.append(line)
    if data["misconceptions"]:
        md += ["", "## Вероятные заблуждения"]
        for m in data["misconceptions"]:
            md.append(
                f"- {m['skill_name']}: {m['description']} "
                f"(уверенность {m['confidence']:.0%}, сигнал {m['signal_strength_ru']})"
            )
    if data["skills"]:
        md += ["", "## Навыки на этом уроке"]
        for s in data["skills"]:
            md.append(f"- {s['skill_name']} — {s['status_ru']}")
    if data["limitations"]:
        md += ["", "## Ограничения анализа"] + [f"- {x}" for x in data["limitations"]]
    if data.get("language_notes"):
        md += ["", f"*Языковые заметки:* {data['language_notes']}"]
    return "\n".join(md).strip() + "\n"


# ---------------------------------------------------------------- manager


def build_manager_report_data(a: LessonAnalysis, ctx: ReportContext) -> dict:
    """Audience: head / methodist — quality indicators and counts, no teacher rating."""
    mi = a.manager_indicators
    skills_by_status = {"struggling": 0, "developing": 0, "mastered": 0}
    for s in a.skills:
        skills_by_status[s.status] += 1
    counts = {
        "errors": len(a.errors),
        "errors_repeated_after_explanation": sum(1 for e in a.errors if e.repeated_after_explanation),
        "errors_corrected": sum(1 for e in a.errors if e.corrected),
        "independent_successes": len(a.independent_successes),
        "tasks_total": mi.tasks_total,
        "tasks_solved_independently": mi.tasks_solved_independently,
        "objectives_total": len(a.learning_objectives),
        "objectives_checked": sum(1 for o in a.learning_objectives if o.was_checked),
        "skills_struggling": skills_by_status["struggling"],
        "skills_developing": skills_by_status["developing"],
        "skills_mastered": skills_by_status["mastered"],
        "visual_issues": sum(1 for v in a.visual_observations if v.issue),
        "misconceptions_strong": sum(1 for m in a.misconceptions if m.signal_strength == "strong"),
        "re_explanations": sum(1 for ev in a.timeline if ev.kind == "re_explanation"),
    }
    key_episodes = [
        {
            "timecode": fmt_ts(e.timestamp),
            "skill_name": e.skill_name,
            "description": e.description,
            "repeated_after_explanation": e.repeated_after_explanation,
            "corrected": e.corrected,
        }
        for e in sorted(a.errors, key=lambda e: (not e.repeated_after_explanation, e.timestamp))[:5]
    ]
    return {
        "kind": "manager",
        "lesson_id": str(ctx.lesson_id),
        "student_id": str(ctx.student_id),
        "subject": a.subject or subject_title(ctx.subject),
        "topic": a.topic,
        "date": format_date_ru(ctx.scheduled_at, ctx.timezone),
        "lesson_date_iso": ctx.scheduled_at.isoformat() if ctx.scheduled_at else None,
        "teacher_name": ctx.teacher_name,
        "student_first_name": ctx.student_first_name,
        "summary": a.lesson_summary,
        "indicators": {
            "objectives_checked": mi.objectives_checked,
            "difficulties_detected": mi.difficulties_detected,
            "re_explanation_happened": mi.re_explanation_happened,
            "needs_methodist_review": mi.needs_methodist_review,
        },
        "needs_review": mi.needs_methodist_review,
        "review_reason": mi.review_reason,
        "talk_shares": {"teacher": mi.teacher_talk_share, "student": mi.student_talk_share},
        "counts": counts,
        "objectives": [o.model_dump() for o in a.learning_objectives],
        "structure_notes": mi.lesson_structure_notes,
        "key_episodes": key_episodes,
        "limitations": list(a.limitations),
        "language_notes": a.language_notes,
    }


def _yes_no(v: bool) -> str:
    return "да" if v else "нет"


def render_manager_markdown(data: dict) -> str:
    """Russian markdown of the manager/methodist report."""
    ind, c = data["indicators"], data["counts"]
    md = [f"# Отчёт для методиста — {data['topic']}"]
    meta = []
    if data.get("date"):
        meta.append(f"**Дата:** {data['date']}")
    if data.get("teacher_name"):
        meta.append(f"**Преподаватель:** {data['teacher_name']}")
    if data.get("student_first_name"):
        meta.append(f"**Ученик:** {data['student_first_name']}")
    if meta:
        md.append(" · ".join(meta))
    md += ["", "## Индикаторы качества"]
    md.append(f"- Цели проверены наблюдаемым способом: **{_yes_no(ind['objectives_checked'])}**")
    md.append(f"- Трудности выявлены: **{_yes_no(ind['difficulties_detected'])}**")
    md.append(f"- Было переобъяснение: **{_yes_no(ind['re_explanation_happened'])}**")
    review = "**ДА**" if data["needs_review"] else "нет"
    md.append(
        f"- Требуется разбор методиста: {review}" + (f" — {data['review_reason']}" if data.get("review_reason") else "")
    )
    md += ["", "## Показатели"]
    shares = data["talk_shares"]
    if shares.get("teacher") is not None or shares.get("student") is not None:
        t = f"{shares['teacher']:.0%}" if shares.get("teacher") is not None else "н/д"
        s = f"{shares['student']:.0%}" if shares.get("student") is not None else "н/д"
        md.append(f"- Доли речи: учитель {t}, ученик {s}")
    md.append(f"- Заданий: {c['tasks_total']}, решено самостоятельно: {c['tasks_solved_independently']}")
    md.append(f"- Целей: {c['objectives_total']}, проверено: {c['objectives_checked']}")
    md.append(
        f"- Ошибок: {c['errors']} (повторились после объяснения: {c['errors_repeated_after_explanation']}, "
        f"исправлены: {c['errors_corrected']}); самостоятельных успехов: {c['independent_successes']}"
    )
    md.append(
        f"- Навыки: трудности {c['skills_struggling']}, в развитии {c['skills_developing']}, "
        f"освоено {c['skills_mastered']}"
    )
    if c["visual_issues"]:
        md.append(f"- Замечаний по написанному на экране: {c['visual_issues']}")
    md += ["", "## Резюме урока", data["summary"]]
    if data.get("structure_notes"):
        md += ["", "## Структура урока", data["structure_notes"]]
    if data["key_episodes"]:
        md += ["", "## Ключевые эпизоды"]
        for e in data["key_episodes"]:
            flags = []
            if e["repeated_after_explanation"]:
                flags.append("повтор после объяснения")
            flags.append("исправлена" if e["corrected"] else "не исправлена")
            md.append(f"- [{e['timecode']}] {e['skill_name']}: {e['description']} ({', '.join(flags)})")
    if data["limitations"]:
        md += ["", "## Ограничения анализа"] + [f"- {x}" for x in data["limitations"]]
    return "\n".join(md).strip() + "\n"


# ---------------------------------------------------------------- parent


def build_parent_report_data(a: LessonAnalysis, ctx: ReportContext) -> dict:
    """Audience: the parent — plain language, no teacher evaluation, no diagnoses."""
    pr = a.parent_report
    snapshot: dict[str, list[str]] = {"struggling": [], "developing": [], "mastered": []}
    for s in a.skills:
        snapshot[s.status].append(s.skill_name)
    return {
        "kind": "parent",
        "lesson_id": str(ctx.lesson_id),
        "student_id": str(ctx.student_id),
        "subject": a.subject or subject_title(ctx.subject),
        "topic": a.topic,
        "topic_plain": pr.topic_plain,
        "date": format_date_ru(ctx.scheduled_at, ctx.timezone),
        "lesson_date_iso": ctx.scheduled_at.isoformat() if ctx.scheduled_at else None,
        "child_first_name": ctx.student_first_name,
        "what_child_did_well": list(pr.what_child_did_well),
        "what_was_hard": list(pr.what_was_hard),
        "what_to_repeat": list(pr.what_to_repeat),
        "home_task_suggestion": pr.home_task_suggestion,
        "encouragement": pr.encouragement,
        "progress_note": pr.progress_note,
        "skills_snapshot": snapshot,
        "skills_snapshot_counts": {k: len(v) for k, v in snapshot.items()},
    }


def render_parent_markdown(data: dict) -> str:
    """Russian markdown of the parent report (for the parent portal)."""
    title = f"# {data['subject']}" + (f" — {data['date']}" if data.get("date") else "")
    md = [title]
    if data.get("child_first_name"):
        md.append(f"**Ребёнок:** {data['child_first_name']}")
    md += ["", "## Тема урока", data["topic_plain"]]
    if data["what_child_did_well"]:
        md += ["", "## Что получилось самостоятельно"] + [f"- {x}" for x in data["what_child_did_well"]]
    if data["what_was_hard"]:
        md += ["", "## Где были ошибки"] + [f"- {x}" for x in data["what_was_hard"]]
    if data["what_to_repeat"]:
        md += ["", "## Что повторить дома (10–15 минут)"] + [f"- {x}" for x in data["what_to_repeat"]]
    if data.get("home_task_suggestion"):
        md += ["", f"**Идея для домашнего задания:** {data['home_task_suggestion']}"]
    if data.get("progress_note"):
        md += ["", f"**Динамика:** {data['progress_note']}"]
    md += ["", f"*{data['encouragement']}*"]
    return "\n".join(md).strip() + "\n"


@dataclass(slots=True)
class _HtmlLine:
    """One Telegram line: ``prefix`` and ``suffix`` are trusted HTML, ``text`` is escaped at render time."""

    text: str
    prefix: str = ""
    suffix: str = ""

    def render(self) -> str:
        return f"{self.prefix}{html.escape(_clean(self.text), quote=False)}{self.suffix}"


def _shorten(text: str, limit: int) -> str:
    text = _clean(text)
    if len(text) <= limit:
        return text
    cut = text[: max(0, limit - 1)]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:—-") + "…"


def render_parent_report_telegram_html(report_data: dict, portal_url: str | None) -> str:
    """Render the parent report as aiogram-safe HTML of at most :data:`TELEGRAM_MAX_CHARS` characters.

    Only ``<b>``, ``<i>`` and ``<a>`` tags are used (no lists); every user-facing string is escaped. When the
    content does not fit, lines are dropped from the end, the last kept line is shortened with «…» and the
    footer «Полный отчёт — в кабинете родителя» (a link when ``portal_url`` is given) is always present.
    """
    d = report_data
    subject = d.get("subject") or "Урок"
    date = d.get("date") or ""
    name = d.get("child_first_name")
    header_text = f"{name} — {subject.lower() if subject[:1].isupper() else subject}" if name else subject
    if date:
        header_text += f", {date}"
    lines: list[_HtmlLine] = [_HtmlLine(header_text, "<b>", "</b>")]
    if d.get("topic_plain"):
        lines.append(_HtmlLine(d["topic_plain"], "<i>Тема:</i> "))

    def section(title: str, items: Sequence[str]) -> None:
        items = [i for i in items if _clean(i)]
        if not items:
            return
        lines.append(_HtmlLine(""))
        lines.append(_HtmlLine(title, "<b>", "</b>"))
        lines.extend(_HtmlLine(item, "• ") for item in items)

    section("Получилось самостоятельно", d.get("what_child_did_well") or [])
    section("Где были ошибки", d.get("what_was_hard") or [])
    section("Что повторить дома", d.get("what_to_repeat") or [])
    if d.get("home_task_suggestion"):
        lines.append(_HtmlLine(d["home_task_suggestion"], "<i>Домашнее задание:</i> "))
    if d.get("progress_note"):
        lines.append(_HtmlLine(""))
        lines.append(_HtmlLine(d["progress_note"], "<i>Динамика:</i> "))
    if d.get("encouragement"):
        lines.append(_HtmlLine(""))
        lines.append(_HtmlLine(d["encouragement"], "<i>", "</i>"))

    if portal_url:
        footer = f'<a href="{html.escape(portal_url, quote=True)}">{PORTAL_FOOTER_TEXT}</a>'
    else:
        footer = PORTAL_FOOTER_TEXT
    budget = TELEGRAM_MAX_CHARS - len(footer) - 2  # "\n\n" before the footer

    rendered: list[str] = []
    used = 0
    truncated = False
    for line in lines:
        piece = line.render()
        extra = len(piece) + (1 if rendered else 0)
        if used + extra <= budget:
            rendered.append(piece)
            used += extra
            continue
        remaining = budget - used - (1 if rendered else 0)
        overhead = len(line.prefix) + len(line.suffix)
        if remaining - overhead >= 24:
            short = _HtmlLine(_shorten(line.text, remaining - overhead), line.prefix, line.suffix).render()
            if len(short) <= remaining:
                rendered.append(short)
                used += len(short) + (1 if len(rendered) > 1 else 0)
        truncated = True
        break
    if truncated:
        # drop a trailing blank / header-only tail and mark the cut
        while rendered and (rendered[-1] == "" or (rendered[-1].startswith("<b>") and rendered[-1].endswith("</b>"))):
            rendered.pop()
        if rendered and not rendered[-1].endswith("…") and not rendered[-1].endswith("…</i>"):
            rendered[-1] = rendered[-1] + "…"
    body = "\n".join(rendered).strip()
    result = f"{body}\n\n{footer}" if body else footer
    if len(result) > TELEGRAM_MAX_CHARS:  # defensive: should not happen, but never exceed the limit
        result = f"{body[: TELEGRAM_MAX_CHARS - len(footer) - 3].rstrip()}…\n\n{footer}"
    return result


# ---------------------------------------------------------------- persistence


async def next_report_version(session: AsyncSession, lesson_id: uuid.UUID, kind: ReportKind) -> int:
    """``max(version) + 1`` for ``(lesson_id, kind)`` (unique constraint ``uq_reports_lesson_kind_version``)."""
    current = (
        await session.execute(
            select(func.max(Report.version)).where(Report.lesson_id == lesson_id, Report.kind == kind)
        )
    ).scalar_one_or_none()
    return int(current or 0) + 1


async def load_report_context(session: AsyncSession, lesson: Lesson) -> ReportContext:
    """Collect student/teacher/centre/frames facts for ``lesson`` without relying on loaded relationships."""
    student = await session.get(Student, lesson.student_id)
    teacher = await session.get(Teacher, lesson.teacher_id)
    center = await session.get(Center, lesson.center_id)
    frames = (await session.execute(select(Frame).where(Frame.lesson_id == lesson.id))).scalars().all()
    return ReportContext(
        lesson_id=lesson.id,
        student_id=lesson.student_id,
        scheduled_at=lesson.scheduled_at,
        subject=lesson.subject or "russian",
        timezone=(center.timezone if center is not None else DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE,
        student_first_name=first_name(student.full_name) if student is not None else None,
        teacher_name=teacher.full_name if teacher is not None else None,
        frames_by_index={f.index: {"frame_id": str(f.id), "timestamp_sec": f.timestamp_sec} for f in frames},
    )


def build_report_payloads(a: LessonAnalysis, ctx: ReportContext) -> dict[ReportKind, tuple[dict, str, str | None]]:
    """Pure part of :func:`build_reports`: ``{kind: (data, markdown, telegram_html)}``."""
    teacher = build_teacher_report_data(a, ctx)
    manager = build_manager_report_data(a, ctx)
    parent = build_parent_report_data(a, ctx)
    return {
        ReportKind.teacher: (teacher, render_teacher_markdown(teacher), None),
        ReportKind.manager: (manager, render_manager_markdown(manager), None),
        ReportKind.parent: (parent, render_parent_markdown(parent), render_parent_report_telegram_html(parent, None)),
    }


async def build_reports(session: AsyncSession, lesson: Lesson, analysis: Analysis) -> list[Report]:
    """Create teacher / manager / parent ``Report`` rows (new version each) from ``analysis``; flushed, not committed.

    The parent report's ``telegram_html`` is rendered without a portal link; module C re-renders it with a
    fresh magic link via :func:`render_parent_report_telegram_html` at send time.
    """
    a = LessonAnalysis.model_validate(analysis.data)
    ctx = await load_report_context(session, lesson)
    now = datetime.now(UTC)
    reports: list[Report] = []
    for kind, (data, markdown, tg_html) in build_report_payloads(a, ctx).items():
        version = await next_report_version(session, lesson.id, kind)
        report = Report(
            lesson_id=lesson.id,
            kind=kind,
            language=Language.ru,
            data=data,
            markdown=markdown,
            telegram_html=tg_html,
            version=version,
            published_at=now,
        )
        session.add(report)
        reports.append(report)
    await session.flush()
    log.info("reports_built", lesson_id=str(lesson.id), versions={r.kind.value: r.version for r in reports})
    return reports
