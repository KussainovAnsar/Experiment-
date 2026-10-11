"""/api/students/{id}/progress — skill states and per-lesson dynamics (staff). Parent variant lives in parent.py."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentStaff, DbSession, is_teacher, not_found
from app.api.people import get_student_or_404
from app.db.models import Lesson, SkillState, SkillStatus
from app.logging import get_logger
from app.schemas.api import SkillsSummary

router = APIRouter(prefix="/api", tags=["progress"])
log = get_logger(__name__)


async def progress_summary(session: AsyncSession, student_id: uuid.UUID) -> dict[str, Any]:
    """Call module B2's ``student_progress_summary``; 503 when the module is not available."""
    try:
        from app.pipeline.progress import student_progress_summary
    except ImportError as exc:
        log.warning("progress_module_missing", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Модуль прогресса недоступен"
        ) from exc
    return await student_progress_summary(session, student_id)


async def skills_summary(session: AsyncSession, student_id: uuid.UUID) -> SkillsSummary:
    """Count skill states per status for a student (cheap aggregate used in cards)."""
    rows = await session.execute(
        select(SkillState.status, func.count()).where(SkillState.student_id == student_id).group_by(SkillState.status)
    )
    counts = {status_value: count for status_value, count in rows.all()}
    return SkillsSummary(
        struggling=counts.get(SkillStatus.struggling, 0),
        developing=counts.get(SkillStatus.developing, 0),
        mastered=counts.get(SkillStatus.mastered, 0),
    )


@router.get("/students/{student_id}/progress")
async def student_progress(student_id: uuid.UUID, user: CurrentStaff, session: DbSession) -> dict[str, Any]:
    """Progress summary: ``{"skills": [...], "lessons": [...], "counts": {...}}``.

    Teachers only see progress of students they have lessons with.
    """
    student = await get_student_or_404(session, user, student_id)
    if is_teacher(user):
        taught = (
            await session.execute(
                select(Lesson.id).where(Lesson.student_id == student.id, Lesson.teacher_id == user.teacher_id).limit(1)
            )
        ).first()
        if taught is None:
            raise not_found("Ученик")
    return await progress_summary(session, student.id)
