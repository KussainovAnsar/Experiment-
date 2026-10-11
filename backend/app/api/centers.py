"""/api/centers/me and /api/dashboard."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import CurrentStaff, DbSession, ManagerStaff, is_teacher
from app.api.lessons import row_to_lesson_dto, staff_lessons_query
from app.db.models import Analysis, Lesson, LessonStatus
from app.schemas.api import CenterOut, CenterUpdate, DashboardOut

router = APIRouter(prefix="/api", tags=["centers"])

PROCESSING_STATUSES = [s for s in LessonStatus if s not in (LessonStatus.done, LessonStatus.failed)]


@router.get("/centers/me", response_model=CenterOut)
async def get_center(user: CurrentStaff) -> CenterOut:
    """The caller's centre."""
    return CenterOut.model_validate(user.center)


@router.patch("/centers/me", response_model=CenterOut)
async def update_center(body: CenterUpdate, user: ManagerStaff, session: DbSession) -> CenterOut:
    """Update centre profile / settings (owner, methodist)."""
    center = user.center
    for key, value in body.model_dump(exclude_unset=True).items():
        if key == "settings" and value is not None:
            center.settings = {**(center.settings or {}), **value}
        else:
            setattr(center, key, value)
    await session.commit()
    await session.refresh(center)
    return CenterOut.model_validate(center)


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(user: CurrentStaff, session: DbSession) -> DashboardOut:
    """Centre overview: volumes, share of lessons with checked objectives, needs-review queue, recent lessons.

    «За последние 7 дней» counts by lesson date (``scheduled_at``), like every other list in the UI — not by
    upload time. The needs-review indicator is manager-only: teachers get ``null`` / an empty list.
    """
    base = staff_lessons_query(user)
    visible_ids = select(base.order_by(None).with_only_columns(Lesson.id).subquery().c.id)

    async def _count(*clauses) -> int:  # noqa: ANN002
        stmt = select(func.count()).select_from(Lesson).where(Lesson.id.in_(visible_ids), *clauses)
        return (await session.execute(stmt)).scalar_one()

    week_ago = datetime.now(UTC) - timedelta(days=7)
    lessons_total = await _count()
    lessons_last_7 = await _count(Lesson.scheduled_at >= week_ago)
    lessons_processing = await _count(Lesson.status.in_(PROCESSING_STATUSES))
    lessons_failed = await _count(Lesson.status == LessonStatus.failed)

    objectives_checked = Analysis.data["manager_indicators"]["objectives_checked"].astext == "true"
    needs_review = Analysis.data["manager_indicators"]["needs_methodist_review"].astext == "true"
    done_ids = select(Lesson.id).where(Lesson.id.in_(visible_ids), Lesson.status == LessonStatus.done)

    async def _count_analyses(*clauses) -> int:  # noqa: ANN002
        stmt = select(func.count()).select_from(Analysis).where(Analysis.lesson_id.in_(done_ids), *clauses)
        return (await session.execute(stmt)).scalar_one()

    done_total = await _count_analyses()
    checked_total = await _count_analyses(objectives_checked)
    share = (checked_total / done_total) if done_total else None

    recent_rows = await session.execute(base.order_by(Lesson.created_at.desc()).limit(10))
    needs_review_count: int | None = None
    review_lessons: list = []
    if not is_teacher(user):
        needs_review_count = await _count_analyses(needs_review)
        review_rows = await session.execute(
            base.join(Analysis, Analysis.lesson_id == Lesson.id)
            .where(Lesson.status == LessonStatus.done, needs_review)
            .order_by(Lesson.scheduled_at.desc())
            .limit(10)
        )
        review_lessons = [row_to_lesson_dto(r) for r in review_rows.all()]
    return DashboardOut(
        lessons_total=lessons_total,
        lessons_last_7_days=lessons_last_7,
        lessons_processing=lessons_processing,
        lessons_failed=lessons_failed,
        share_objectives_checked=round(share, 3) if share is not None else None,
        needs_review_count=needs_review_count,
        recent_lessons=[row_to_lesson_dto(r) for r in recent_rows.all()],
        needs_review_lessons=review_lessons,
    )
