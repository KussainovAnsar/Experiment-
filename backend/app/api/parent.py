"""/api/parent/* — parent portal: magic-link exchange, children, lesson reports, progress."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentParent, DbSession, not_found
from app.api.lessons import frame_url
from app.api.progress import progress_summary, skills_summary
from app.api.reports import latest_reports
from app.db.models import (
    ActorType,
    Center,
    Frame,
    Lesson,
    LessonStatus,
    Parent,
    ParentStudent,
    Report,
    ReportKind,
    Student,
    Teacher,
)
from app.logging import get_logger
from app.schemas.api import (
    ParentExchangeRequest,
    ParentExchangeResponse,
    ParentFrameOut,
    ParentLessonCard,
    ParentLessonReportOut,
    ParentMeOut,
    ParentMeUpdate,
    ParentStudentCard,
    StudentBrief,
)
from app.services.audit import audit
from app.services.auth import create_parent_token, exchange_magic_link

router = APIRouter(prefix="/api/parent", tags=["parent"])
log = get_logger(__name__)


async def _parent_me(session: AsyncSession, parent: Parent) -> ParentMeOut:
    center = await session.get(Center, parent.center_id)
    return ParentMeOut(
        id=parent.id,
        full_name=parent.full_name,
        preferred_channel=parent.preferred_channel,
        preferred_language=parent.preferred_language,
        telegram_linked=parent.telegram_chat_id is not None,
        center_name=center.name if center else "",
    )


async def linked_students(session: AsyncSession, parent: Parent) -> list[Student]:
    """Students linked to the parent (alphabetical)."""
    rows = await session.execute(
        select(Student)
        .join(ParentStudent, ParentStudent.student_id == Student.id)
        .where(ParentStudent.parent_id == parent.id)
        .order_by(Student.full_name)
    )
    return list(rows.scalars())


async def get_linked_student_or_404(session: AsyncSession, parent: Parent, student_id: uuid.UUID) -> Student:
    """A student linked to this parent, else 404 (never reveals other children)."""
    row = await session.execute(
        select(Student)
        .join(ParentStudent, ParentStudent.student_id == Student.id)
        .where(ParentStudent.parent_id == parent.id, Student.id == student_id)
    )
    student = row.scalar_one_or_none()
    if student is None:
        raise not_found("Ученик")
    return student


def _brief(student: Student) -> StudentBrief:
    return StudentBrief(id=student.id, full_name=student.full_name, grade=student.grade)


@router.post("/auth/exchange", response_model=ParentExchangeResponse)
async def exchange(body: ParentExchangeRequest, session: DbSession) -> ParentExchangeResponse:
    """Exchange a magic-link token for a parent JWT. The link stays reusable until it expires."""
    parent = await exchange_magic_link(session, body.token)
    if parent is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Ссылка недействительна или устарела. Запросите новую."
        )
    students = await linked_students(session, parent)
    await audit(
        session, center_id=parent.center_id, actor_type=ActorType.parent, actor_id=parent.id, action="parent_login"
    )
    await session.commit()
    log.info("parent_login", parent_id=str(parent.id), center_id=str(parent.center_id))
    return ParentExchangeResponse(
        access_token=create_parent_token(parent),
        parent=await _parent_me(session, parent),
        students=[_brief(s) for s in students],
    )


@router.get("/me", response_model=ParentMeOut)
async def me(parent: CurrentParent, session: DbSession) -> ParentMeOut:
    """The authenticated parent."""
    return await _parent_me(session, parent)


@router.patch("/me", response_model=ParentMeOut)
async def update_me(body: ParentMeUpdate, parent: CurrentParent, session: DbSession) -> ParentMeOut:
    """Update notification channel / language preferences."""
    for key, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(parent, key, value)
    await session.commit()
    return await _parent_me(session, parent)


@router.get("/students", response_model=list[ParentStudentCard])
async def my_students(parent: CurrentParent, session: DbSession) -> list[ParentStudentCard]:
    """Children of the parent with last lesson date, lesson count and skill summary."""
    cards: list[ParentStudentCard] = []
    for student in await linked_students(session, parent):
        count, last_at = (
            await session.execute(
                select(func.count(), func.max(Lesson.scheduled_at)).where(
                    Lesson.student_id == student.id, Lesson.status == LessonStatus.done
                )
            )
        ).one()
        cards.append(
            ParentStudentCard(
                student=_brief(student),
                last_lesson_at=last_at,
                lessons_count=count,
                skills_summary=await skills_summary(session, student.id),
            )
        )
    return cards


@router.get("/students/{student_id}/lessons", response_model=list[ParentLessonCard])
async def student_lessons(student_id: uuid.UUID, parent: CurrentParent, session: DbSession) -> list[ParentLessonCard]:
    """Done lessons of a child, newest first, each with a short summary of the latest parent report."""
    student = await get_linked_student_or_404(session, parent, student_id)
    lessons = (
        await session.execute(
            select(Lesson)
            .where(Lesson.student_id == student.id, Lesson.status == LessonStatus.done)
            .order_by(Lesson.scheduled_at.desc())
        )
    ).scalars()
    lesson_list = list(lessons)
    if not lesson_list:
        return []
    reports = (
        await session.execute(
            select(Report)
            .where(Report.lesson_id.in_([lesson.id for lesson in lesson_list]), Report.kind == ReportKind.parent)
            .order_by(Report.version.desc())
        )
    ).scalars()
    latest: dict[uuid.UUID, Report] = {}
    for report in reports:
        latest.setdefault(report.lesson_id, report)
    cards: list[ParentLessonCard] = []
    for lesson in lesson_list:
        data: dict[str, Any] = latest[lesson.id].data if lesson.id in latest else {}
        cards.append(
            ParentLessonCard(
                lesson_id=lesson.id,
                scheduled_at=lesson.scheduled_at,
                topic_plain=data.get("topic_plain"),
                encouragement=data.get("encouragement"),
                what_to_repeat_count=len(data.get("what_to_repeat") or []),
            )
        )
    return cards


@router.get("/students/{student_id}/lessons/{lesson_id}", response_model=ParentLessonReportOut)
async def lesson_report(
    student_id: uuid.UUID, lesson_id: uuid.UUID, parent: CurrentParent, session: DbSession
) -> ParentLessonReportOut:
    """Full parent report of one lesson with frame links."""
    student = await get_linked_student_or_404(session, parent, student_id)
    lesson = (
        await session.execute(
            select(Lesson).where(
                Lesson.id == lesson_id, Lesson.student_id == student.id, Lesson.status == LessonStatus.done
            )
        )
    ).scalar_one_or_none()
    if lesson is None:
        raise not_found("Урок")
    report = (await latest_reports(session, lesson.id)).get(ReportKind.parent)
    if report is None:
        raise not_found("Отчёт")
    teacher = await session.get(Teacher, lesson.teacher_id)
    frames = (await session.execute(select(Frame).where(Frame.lesson_id == lesson.id).order_by(Frame.index))).scalars()
    return ParentLessonReportOut(
        lesson_id=lesson.id,
        scheduled_at=lesson.scheduled_at,
        teacher_name=teacher.full_name if teacher else "",
        data=report.data,
        frames=[ParentFrameOut(index=f.index, timestamp_sec=f.timestamp_sec, url=frame_url(f.id)) for f in frames],
    )


@router.get("/students/{student_id}/progress")
async def student_progress(student_id: uuid.UUID, parent: CurrentParent, session: DbSession) -> dict[str, Any]:
    """Skill states + per-lesson dynamics of a child (same shape as the staff endpoint)."""
    student = await get_linked_student_or_404(session, parent, student_id)
    return await progress_summary(session, student.id)
