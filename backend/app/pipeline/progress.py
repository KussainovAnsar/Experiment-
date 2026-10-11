"""Student progress: merge a lesson's ``LessonAnalysis`` into ``skill_states`` and summarise it.

Rules (docs/CONTRACT.md §4 / §5 and module B2 spec):

* every observed skill gets one *summary* evidence entry per lesson plus the analysis' evidence items and
  the lesson's errors for that skill, so ``times_seen`` / ``times_struggled`` can be **recomputed from
  evidence** — reprocessing the same lesson first drops its previous entries and therefore never double counts;
* ``times_struggled`` counts lessons where the status was ``struggling`` or an error was recorded;
* the new status is the analysis status, but a skill becomes ``mastered`` only when it was seen in ≥ 2 lessons
  and neither of the latest two lessons had struggling — otherwise it is capped at ``developing``;
* evidence keeps the last :data:`MAX_EVIDENCE` items (per-lesson summary entries are dropped last).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Analysis, Lesson, SkillState, SkillStatus
from app.logging import get_logger
from app.schemas.analysis import RUSSIAN_SKILL_KEYS, LessonAnalysis

log = get_logger("app.pipeline.progress")

MAX_EVIDENCE = 20
_STATUS_ORDER = {SkillStatus.struggling: 0, SkillStatus.developing: 1, SkillStatus.mastered: 2}


@dataclass(slots=True)
class SkillObservation:
    """What one analysis says about one skill."""

    skill_key: str
    skill_name: str
    status: str  # struggling | developing | mastered
    entries: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------- collecting observations


def _entry(
    lesson: Lesson,
    *,
    kind: str,
    status: str,
    note: str,
    timestamp_sec: float | None,
    frame_ids: list[int] | None = None,
) -> dict:
    return {
        "lesson_id": str(lesson.id),
        "lesson_at": lesson.scheduled_at.isoformat() if lesson.scheduled_at else None,
        "timestamp_sec": timestamp_sec,
        "note": note[:300],
        "status": status,
        "kind": kind,  # summary | evidence | error
        "frame_ids": list(frame_ids or []),
    }


def collect_observations(a: LessonAnalysis, lesson: Lesson) -> dict[str, SkillObservation]:
    """Group the analysis' skills and errors by ``skill_key`` into observations with evidence entries."""
    obs: dict[str, SkillObservation] = {}
    for s in a.skills:
        o = obs.setdefault(s.skill_key, SkillObservation(s.skill_key, s.skill_name, s.status))
        o.status = s.status
        o.skill_name = s.skill_name or o.skill_name
        for ev in s.evidence:
            o.entries.append(
                _entry(
                    lesson,
                    kind="evidence",
                    status=s.status,
                    note=ev.description,
                    timestamp_sec=ev.timestamp,
                    frame_ids=ev.frame_ids,
                )
            )
    for e in a.errors:
        o = obs.get(e.skill_key)
        if o is None:
            name = e.skill_name or RUSSIAN_SKILL_KEYS.get(e.skill_key, e.skill_key)
            o = obs[e.skill_key] = SkillObservation(e.skill_key, name, "struggling")
        o.entries.append(
            _entry(
                lesson,
                kind="error",
                status="struggling",
                note=f"Ошибка: {e.description}",
                timestamp_sec=e.timestamp,
                frame_ids=e.frame_ids,
            )
        )
    for o in obs.values():
        o.skill_name = o.skill_name or RUSSIAN_SKILL_KEYS.get(o.skill_key, o.skill_key)
        first_ts = min((en["timestamp_sec"] for en in o.entries if en["timestamp_sec"] is not None), default=None)
        status_ru = {"struggling": "трудности", "developing": "в развитии", "mastered": "освоено"}[o.status]
        summary = _entry(
            lesson, kind="summary", status=o.status, note=f"Итог урока: статус «{status_ru}»", timestamp_sec=first_ts
        )
        o.entries.insert(0, summary)
    return obs


# ---------------------------------------------------------------- evidence math


def _lesson_order(evidence: list[dict]) -> list[str]:
    """Distinct lesson ids ordered chronologically (by ``lesson_at``; unknown dates keep appearance order)."""
    first_seen: dict[str, tuple[int, str]] = {}
    for pos, e in enumerate(evidence):
        lid = str(e.get("lesson_id") or "")
        if not lid:
            continue
        at = str(e.get("lesson_at") or "")
        if lid not in first_seen or (at and not first_seen[lid][1]):
            first_seen[lid] = (pos, at)
    # lessons with a date sort by date; undated ones by position, after dated ones of the same position prefix
    return sorted(first_seen, key=lambda lid: (first_seen[lid][1] or "9999", first_seen[lid][0]))


def _struggled_lessons(evidence: list[dict]) -> set[str]:
    return {
        str(e.get("lesson_id"))
        for e in evidence
        if e.get("lesson_id") and (e.get("status") == "struggling" or e.get("kind") == "error")
    }


def trim_evidence(evidence: list[dict], limit: int = MAX_EVIDENCE) -> list[dict]:
    """Keep at most ``limit`` entries: oldest non-summary entries go first, then oldest summaries."""
    items = list(evidence)
    while len(items) > limit:
        for i, e in enumerate(items):
            if e.get("kind") != "summary":
                del items[i]
                break
        else:
            del items[0]
    return items


def resolve_status(requested: str, lessons: list[str], struggled: set[str]) -> SkillStatus:
    """Apply the mastery rule to the analysis' requested status."""
    if requested == "mastered":
        latest_two = lessons[-2:]
        if len(lessons) < 2 or any(lid in struggled for lid in latest_two):
            return SkillStatus.developing
        return SkillStatus.mastered
    return SkillStatus(requested)


# ---------------------------------------------------------------- public API


async def update_progress(session: AsyncSession, lesson: Lesson, analysis: Analysis) -> list[SkillState]:
    """Merge ``analysis`` into the student's ``skill_states``; returns the touched rows (flushed, not committed)."""
    a = LessonAnalysis.model_validate(analysis.data)
    observations = collect_observations(a, lesson)
    if not observations:
        log.info("progress_no_skills", lesson_id=str(lesson.id))
        return []
    rows = (
        await session.execute(
            select(SkillState).where(
                SkillState.student_id == lesson.student_id, SkillState.skill_key.in_(list(observations))
            )
        )
    ).scalars()
    states: dict[str, SkillState] = {s.skill_key: s for s in rows}
    lesson_id = str(lesson.id)
    touched: list[SkillState] = []
    for key, obs in observations.items():
        state = states.get(key)
        if state is None:
            state = SkillState(
                student_id=lesson.student_id,
                skill_key=key,
                skill_name=obs.skill_name[:200],
                status=SkillStatus.developing,
                evidence=[],
                times_seen=0,
                times_struggled=0,
            )
            session.add(state)
        kept = [e for e in (state.evidence or []) if isinstance(e, dict) and str(e.get("lesson_id")) != lesson_id]
        evidence = trim_evidence([*kept, *obs.entries])
        lessons = _lesson_order(evidence)
        struggled = _struggled_lessons(evidence)
        state.skill_name = (obs.skill_name or state.skill_name)[:200]
        state.status = resolve_status(obs.status, lessons, struggled)
        state.evidence = evidence  # new list object → JSONB change is detected
        state.times_seen = len(lessons)
        state.times_struggled = len(struggled)
        if lessons:
            state.first_seen_lesson_id = _as_uuid(lessons[0]) or state.first_seen_lesson_id or lesson.id
            state.last_seen_lesson_id = _as_uuid(lessons[-1]) or lesson.id
        else:  # pragma: no cover - observations always add at least the summary entry
            state.first_seen_lesson_id = state.first_seen_lesson_id or lesson.id
            state.last_seen_lesson_id = lesson.id
        touched.append(state)
    await session.flush()
    log.info(
        "progress_updated",
        lesson_id=lesson_id,
        student_id=str(lesson.student_id),
        skills={s.skill_key: s.status.value for s in touched},
    )
    return touched


def _as_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


async def student_progress_summary(session: AsyncSession, student_id: uuid.UUID) -> dict:
    """Skills (struggling first), analysed lessons in chronological order and status counts for a student."""
    states = (await session.execute(select(SkillState).where(SkillState.student_id == student_id))).scalars().all()
    skills = [
        {
            "skill_key": s.skill_key,
            "skill_name": s.skill_name,
            "status": s.status.value,
            "times_seen": s.times_seen,
            "times_struggled": s.times_struggled,
            "first_seen_lesson_id": str(s.first_seen_lesson_id) if s.first_seen_lesson_id else None,
            "last_seen_lesson_id": str(s.last_seen_lesson_id) if s.last_seen_lesson_id else None,
            "updated_at": _iso(s.updated_at),
            "evidence": list(s.evidence or []),
        }
        for s in sorted(states, key=lambda s: (_STATUS_ORDER[s.status], s.skill_name))
    ]
    counts: dict[str, int] = defaultdict(int)
    for s in states:
        counts[s.status.value] += 1
    rows = (
        await session.execute(
            select(Lesson, Analysis)
            .join(Analysis, Analysis.lesson_id == Lesson.id)
            .where(Lesson.student_id == student_id)
            .order_by(Lesson.scheduled_at, Lesson.created_at)
        )
    ).all()
    lessons = []
    for lesson, analysis in rows:
        data = analysis.data or {}
        mi = data.get("manager_indicators") or {}
        lessons.append(
            {
                "lesson_id": str(lesson.id),
                "scheduled_at": _iso(lesson.scheduled_at),
                "status": lesson.status.value,
                "topic": data.get("topic"),
                "errors_count": len(data.get("errors") or []),
                "independent_successes_count": len(data.get("independent_successes") or []),
                "tasks_total": int(mi.get("tasks_total") or 0),
                "tasks_solved_independently": int(mi.get("tasks_solved_independently") or 0),
                "skills": [
                    {"skill_key": s.get("skill_key"), "skill_name": s.get("skill_name"), "status": s.get("status")}
                    for s in (data.get("skills") or [])
                ],
            }
        )
    return {
        "student_id": str(student_id),
        "skills": skills,
        "lessons": lessons,
        "counts": {
            "struggling": counts.get("struggling", 0),
            "developing": counts.get("developing", 0),
            "mastered": counts.get("mastered", 0),
        },
    }
