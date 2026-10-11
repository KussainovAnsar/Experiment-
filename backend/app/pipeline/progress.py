"""Student progress: merge a lesson's ``LessonAnalysis`` into ``skill_states`` and summarise it.

Rules (docs/CONTRACT.md §4 / §5 and module B2 spec):

* every observed skill gets one *summary* evidence entry per lesson plus the analysis' evidence items and
  the lesson's errors for that skill, so ``times_seen`` / ``times_struggled`` can be **recomputed from
  evidence** — reprocessing the same lesson first drops its previous entries and therefore never double counts;
  a re-analysis that no longer mentions a skill also *un-counts* it (the lesson's entries are removed from that
  skill; a skill left without evidence is deleted);
* ``times_struggled`` counts lessons where the status was ``struggling`` or an error was recorded;
* the new status is the analysis status, but a skill becomes ``mastered`` only when it was seen in ≥ 2 lessons
  and neither of the latest two lessons had struggling — otherwise it is capped at ``developing``;
* evidence keeps the last :data:`MAX_EVIDENCE` *detail* items; per-lesson summary entries are never dropped, so
  the counts stay exact after more than :data:`MAX_EVIDENCE` lessons;
* ``skill_key`` is normalised to a lower-case slug of at most :data:`SKILL_KEY_MAX_LEN` characters (the
  ``skill_states.skill_key`` column width) — an over-long slug from the LLM can never fail the lesson.
"""

from __future__ import annotations

import re
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
SKILL_KEY_MAX_LEN = 80  # SkillState.skill_key is String(80)
_STATUS_ORDER = {SkillStatus.struggling: 0, SkillStatus.developing: 1, SkillStatus.mastered: 2}
_NON_SLUG_RE = re.compile(r"[^\w]+", re.UNICODE)
_UNDERSCORES_RE = re.compile(r"_+")
_STATUS_RU = {"struggling": "трудности", "developing": "в развитии", "mastered": "освоено"}


def normalize_skill_key(key: str | None) -> str:
    """Lower-case word-character slug of at most :data:`SKILL_KEY_MAX_LEN` characters (``unknown_skill`` when empty)."""
    slug = _UNDERSCORES_RE.sub("_", _NON_SLUG_RE.sub("_", str(key or "").strip().lower())).strip("_")
    slug = slug[:SKILL_KEY_MAX_LEN].rstrip("_")
    return slug or "unknown_skill"


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
        key = normalize_skill_key(s.skill_key)
        o = obs.setdefault(key, SkillObservation(key, s.skill_name, s.status))
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
        key = normalize_skill_key(e.skill_key)
        o = obs.get(key)
        if o is None:
            name = e.skill_name or RUSSIAN_SKILL_KEYS.get(key, key)
            o = obs[key] = SkillObservation(key, name, "struggling")
        note = f"Ошибка: {e.description}" + (" — исправлена в ходе урока" if e.corrected else "")
        o.entries.append(
            _entry(
                lesson,
                kind="error",
                status="struggling",
                note=note,
                timestamp_sec=e.timestamp,
                frame_ids=e.frame_ids,
            )
        )
    for o in obs.values():
        o.skill_name = o.skill_name or RUSSIAN_SKILL_KEYS.get(o.skill_key, o.skill_key)
        first_ts = min((en["timestamp_sec"] for en in o.entries if en["timestamp_sec"] is not None), default=None)
        status_ru = _STATUS_RU[o.status]
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
    """Drop the oldest *detail* entries until at most ``limit`` remain; per-lesson summaries are never dropped.

    ``times_seen`` / ``first_seen_lesson_id`` are derived from the summaries, so the list may exceed ``limit``
    by the number of lessons beyond it (one small summary entry per lesson) rather than under-count.
    """
    items = list(evidence)
    while len(items) > limit:
        for i, e in enumerate(items):
            if e.get("kind") != "summary":
                del items[i]
                break
        else:
            break
    return items


def _latest_summary_status(evidence: list[dict]) -> str | None:
    """Status of the chronologically last per-lesson summary entry, if any."""
    order = {lid: pos for pos, lid in enumerate(_lesson_order(evidence))}
    summaries = [e for e in evidence if e.get("kind") == "summary" and e.get("status") in _STATUS_RU]
    if not summaries:
        return None
    last = max(summaries, key=lambda e: order.get(str(e.get("lesson_id")), -1))
    return str(last["status"])


def resolve_status(requested: str, lessons: list[str], struggled: set[str]) -> SkillStatus:
    """Apply the mastery rule to the analysis' requested status."""
    if requested == "mastered":
        latest_two = lessons[-2:]
        if len(lessons) < 2 or any(lid in struggled for lid in latest_two):
            return SkillStatus.developing
        return SkillStatus.mastered
    return SkillStatus(requested)


# ---------------------------------------------------------------- public API


def _apply_evidence(state: SkillState, entries: list[dict], *, requested_status: str, fallback_lesson: Lesson) -> None:
    """Recompute status, counts and first/last lesson of ``state`` from ``entries`` (then trimmed)."""
    evidence = trim_evidence(entries)
    lessons = _lesson_order(evidence)
    struggled = _struggled_lessons(evidence)
    state.status = resolve_status(requested_status, lessons, struggled)
    state.evidence = evidence  # new list object → JSONB change is detected
    state.times_seen = len(lessons)
    state.times_struggled = len(struggled)
    if lessons:
        state.first_seen_lesson_id = _as_uuid(lessons[0]) or state.first_seen_lesson_id or fallback_lesson.id
        state.last_seen_lesson_id = _as_uuid(lessons[-1]) or fallback_lesson.id
    else:  # pragma: no cover - callers never pass an empty list
        state.first_seen_lesson_id = state.first_seen_lesson_id or fallback_lesson.id
        state.last_seen_lesson_id = fallback_lesson.id


def _without_lesson(evidence: list | None, lesson_id: str) -> list[dict]:
    return [e for e in (evidence or []) if isinstance(e, dict) and str(e.get("lesson_id")) != lesson_id]


async def update_progress(session: AsyncSession, lesson: Lesson, analysis: Analysis) -> list[SkillState]:
    """Merge ``analysis`` into the student's ``skill_states``; returns the touched rows (flushed, not committed).

    Idempotent per lesson in both directions: the lesson's previous entries are replaced for skills the new
    analysis mentions and removed from skills it no longer mentions (reprocess with a different verdict).
    """
    a = LessonAnalysis.model_validate(analysis.data)
    observations = collect_observations(a, lesson)
    rows = (await session.execute(select(SkillState).where(SkillState.student_id == lesson.student_id))).scalars()
    states: dict[str, SkillState] = {s.skill_key: s for s in rows}
    lesson_id = str(lesson.id)
    touched: list[SkillState] = []
    removed: list[str] = []
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
        kept = _without_lesson(state.evidence, lesson_id)
        state.skill_name = (obs.skill_name or state.skill_name)[:200]
        _apply_evidence(state, [*kept, *obs.entries], requested_status=obs.status, fallback_lesson=lesson)
        touched.append(state)
    for key, state in states.items():
        if key in observations:
            continue
        kept = _without_lesson(state.evidence, lesson_id)
        if len(kept) == len(state.evidence or []):
            continue  # this lesson never contributed to the skill
        if not kept:
            await session.delete(state)
            removed.append(key)
            continue
        requested = _latest_summary_status(kept) or state.status.value
        _apply_evidence(state, kept, requested_status=requested, fallback_lesson=lesson)
        touched.append(state)
    if not observations and not touched and not removed:
        log.info("progress_no_skills", lesson_id=lesson_id)
        return []
    await session.flush()
    log.info(
        "progress_updated",
        lesson_id=lesson_id,
        student_id=str(lesson.student_id),
        skills={s.skill_key: s.status.value for s in touched},
        removed=removed,
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
