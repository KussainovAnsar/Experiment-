"""Per-lesson processing claim and stuck-lesson recovery.

Only one pipeline run may own a lesson at a time. :func:`claim_lesson` is a compare-and-set
``UPDATE lessons SET status='probing' WHERE id=:id AND (status IN (uploaded, done, failed) OR stale)`` — the
second of two concurrent runs (double click, duplicate job, retry) affects zero rows and backs off instead of
writing ``audio.tmp.wav`` / frames / report versions over the first one.

A lesson whose intermediate status has not changed for :func:`stale_processing_after` (default 30 minutes,
``PIPELINE_STALE_MINUTES``) is considered abandoned (process killed mid-stage) and may be claimed or reprocessed
again; in inline mode the API additionally marks such lessons ``failed`` at startup
(:func:`sweep_interrupted_lessons`).
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Lesson, LessonStatus
from app.logging import get_logger

log = get_logger("app.pipeline.locking")

DEFAULT_STALE_MINUTES = 30
INTERRUPTED_MESSAGE = "Обработка прервана перезапуском сервера — запустите обработку заново"
INTERRUPTED_ERROR = "[interrupted] processing task did not survive a server restart"

# Statuses a new run may start from without any staleness condition.
CLAIMABLE_STATUSES: tuple[LessonStatus, ...] = (LessonStatus.uploaded, LessonStatus.done, LessonStatus.failed)
# Every non-terminal status (``uploaded`` included: an orphaned upload is never picked up again in inline mode).
ACTIVE_STATUSES: tuple[LessonStatus, ...] = tuple(
    s for s in LessonStatus if s not in (LessonStatus.done, LessonStatus.failed)
)


def stale_processing_after() -> timedelta:
    """How long an intermediate status may stay unchanged before the lesson counts as abandoned."""
    try:
        minutes = int(os.environ.get("PIPELINE_STALE_MINUTES", str(DEFAULT_STALE_MINUTES)))
    except ValueError:
        minutes = DEFAULT_STALE_MINUTES
    return timedelta(minutes=max(1, minutes))


def is_stale(lesson: Lesson, *, now: datetime | None = None) -> bool:
    """True when the lesson sits in an intermediate status older than :func:`stale_processing_after`."""
    if lesson.status in (LessonStatus.done, LessonStatus.failed) or lesson.updated_at is None:
        return False
    now = now or datetime.now(UTC)
    updated = lesson.updated_at if lesson.updated_at.tzinfo else lesson.updated_at.replace(tzinfo=UTC)
    return updated < now - stale_processing_after()


def can_start_processing(lesson: Lesson, *, now: datetime | None = None) -> bool:
    """Reprocess gate: terminal / freshly uploaded lessons, or abandoned intermediate ones."""
    return lesson.status in CLAIMABLE_STATUSES or is_stale(lesson, now=now)


async def claim_lesson(session: AsyncSession, lesson_id: uuid.UUID, *, status_message: str) -> bool:
    """Atomically move the lesson to ``probing``; ``False`` when another run owns it. Commits."""
    cutoff = datetime.now(UTC) - stale_processing_after()
    result = await session.execute(
        update(Lesson)
        .where(Lesson.id == lesson_id, or_(Lesson.status.in_(CLAIMABLE_STATUSES), Lesson.updated_at < cutoff))
        .values(status=LessonStatus.probing, status_message=status_message, error=None)
    )
    await session.commit()
    claimed = int(result.rowcount or 0) == 1
    if not claimed:
        log.warning("lesson_claim_rejected", lesson_id=str(lesson_id))
    return claimed


async def sweep_interrupted_lessons(session: AsyncSession) -> int:
    """Mark every non-terminal lesson ``failed`` (inline mode after a restart: nothing will ever resume them).

    Returns the number of lessons swept. Commits.
    """
    result = await session.execute(
        update(Lesson)
        .where(Lesson.status.in_(ACTIVE_STATUSES))
        .values(status=LessonStatus.failed, status_message=INTERRUPTED_MESSAGE, error=INTERRUPTED_ERROR)
    )
    await session.commit()
    count = int(result.rowcount or 0)
    if count:
        log.warning("lessons_swept_after_restart", count=count)
    return count
