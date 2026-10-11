"""/api/lessons/{id}/reports — teacher / manager / parent reports (staff side)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentStaff, DbSession, is_teacher, not_found
from app.api.lessons import get_lesson_or_404
from app.db.models import ActorType, Report, ReportKind
from app.logging import get_logger
from app.schemas.api import NotificationOut, ReportOut
from app.services.audit import audit

router = APIRouter(prefix="/api/lessons", tags=["reports"])
log = get_logger(__name__)


async def latest_reports(session: AsyncSession, lesson_id: uuid.UUID) -> dict[ReportKind, Report]:
    """Return the latest version of each report kind for a lesson."""
    rows = (
        await session.execute(
            select(Report).where(Report.lesson_id == lesson_id).order_by(Report.kind, Report.version.desc())
        )
    ).scalars()
    latest: dict[ReportKind, Report] = {}
    for report in rows:
        latest.setdefault(report.kind, report)
    return latest


def _visible_kinds(user_is_teacher: bool) -> list[ReportKind]:
    kinds = [ReportKind.teacher, ReportKind.manager, ReportKind.parent]
    if user_is_teacher:
        kinds.remove(ReportKind.manager)
    return kinds


@router.get("/{lesson_id}/reports", response_model=list[ReportOut])
async def list_reports(lesson_id: uuid.UUID, user: CurrentStaff, session: DbSession) -> list[ReportOut]:
    """Latest reports of the lesson in order teacher, manager, parent (teachers do not get ``manager``)."""
    lesson = await get_lesson_or_404(session, user, lesson_id)
    latest = await latest_reports(session, lesson.id)
    return [ReportOut.model_validate(latest[k]) for k in _visible_kinds(is_teacher(user)) if k in latest]


@router.get("/{lesson_id}/reports/{kind}", response_model=ReportOut)
async def get_report(lesson_id: uuid.UUID, kind: ReportKind, user: CurrentStaff, session: DbSession) -> ReportOut:
    """Latest version of one report kind."""
    if kind == ReportKind.manager and is_teacher(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Отчёт для методиста недоступен преподавателю"
        )
    lesson = await get_lesson_or_404(session, user, lesson_id)
    latest = await latest_reports(session, lesson.id)
    report = latest.get(kind)
    if report is None:
        raise not_found("Отчёт")
    return ReportOut.model_validate(report)


@router.post("/{lesson_id}/reports/parent/send", response_model=list[NotificationOut])
async def send_parent_report(lesson_id: uuid.UUID, user: CurrentStaff, session: DbSession) -> list[NotificationOut]:
    """(Re)send the latest parent report to every linked parent via their preferred channel."""
    lesson = await get_lesson_or_404(session, user, lesson_id)
    latest = await latest_reports(session, lesson.id)
    if ReportKind.parent not in latest:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Отчёт для родителя ещё не сформирован")
    try:
        from app.services.notifications import notify_parents_for_lesson
    except ImportError as exc:
        log.warning("notifications_module_missing", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Модуль уведомлений недоступен"
        ) from exc
    notifications = await notify_parents_for_lesson(session, lesson.id)
    await audit(
        session,
        center_id=user.center_id,
        actor_type=ActorType.staff,
        actor_id=user.id,
        action="parent_report_sent",
        entity="lesson",
        entity_id=lesson.id,
        meta={"count": len(notifications)},
    )
    await session.commit()
    log.info("parent_report_sent", lesson_id=str(lesson.id), count=len(notifications))
    return [NotificationOut.model_validate(n) for n in notifications]
