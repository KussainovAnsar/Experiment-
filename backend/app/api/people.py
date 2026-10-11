"""/api/teachers, /api/students, /api/parents — directories, parent↔student links, invites."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentStaff, DbSession, ManagerStaff, PageParams, not_found
from app.db.models import (
    ActorType,
    Lesson,
    Parent,
    ParentStudent,
    StaffUser,
    Student,
    Teacher,
)
from app.logging import get_logger
from app.schemas.api import (
    LinkedStudentOut,
    LinkStudentRequest,
    MagicLinkOut,
    NotificationOut,
    Page,
    ParentCreate,
    ParentOut,
    ParentUpdate,
    StudentCreate,
    StudentOut,
    StudentUpdate,
    TeacherCreate,
    TeacherOut,
    TeacherUpdate,
)
from app.services.audit import audit
from app.services.auth import create_magic_link, issue_unique_invite_code, revoke_parent_access

router = APIRouter(prefix="/api", tags=["people"])
log = get_logger(__name__)


# ---------------------------------------------------------------- shared loaders


async def get_student_or_404(session: AsyncSession, user: StaffUser, student_id: uuid.UUID) -> Student:
    """Student of the caller's centre, else 404."""
    student = await session.get(Student, student_id)
    if student is None or student.center_id != user.center_id:
        raise not_found("Ученик")
    return student


async def get_teacher_or_404(session: AsyncSession, user: StaffUser, teacher_id: uuid.UUID) -> Teacher:
    """Teacher of the caller's centre, else 404."""
    teacher = await session.get(Teacher, teacher_id)
    if teacher is None or teacher.center_id != user.center_id:
        raise not_found("Преподаватель")
    return teacher


async def get_parent_or_404(session: AsyncSession, user: StaffUser, parent_id: uuid.UUID) -> Parent:
    """Parent of the caller's centre with ``student_links`` loaded, else 404."""
    # populate_existing: always re-run the query so links are fresh even if the Parent is already in the identity map.
    parent = (
        await session.execute(
            select(Parent)
            .where(Parent.id == parent_id)
            .options(selectinload(Parent.student_links).selectinload(ParentStudent.student))
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if parent is None or parent.center_id != user.center_id:
        raise not_found("Родитель")
    return parent


def parent_to_dto(parent: Parent) -> ParentOut:
    """Build ``ParentOut`` (requires ``student_links[].student`` loaded)."""
    return ParentOut(
        id=parent.id,
        center_id=parent.center_id,
        full_name=parent.full_name,
        phone=parent.phone,
        email=parent.email,
        preferred_channel=parent.preferred_channel,
        preferred_language=parent.preferred_language,
        telegram_chat_id=parent.telegram_chat_id,
        telegram_linked=parent.telegram_chat_id is not None,
        invite_code=parent.invite_code,
        whatsapp_phone=parent.whatsapp_phone,
        students=[
            LinkedStudentOut(
                id=link.student.id, full_name=link.student.full_name, grade=link.student.grade, relation=link.relation
            )
            for link in parent.student_links
        ],
        created_at=parent.created_at,
    )


async def _count(session: AsyncSession, stmt) -> int:  # noqa: ANN001
    return (await session.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()


def _apply_updates(obj: object, data: dict) -> None:
    for key, value in data.items():
        setattr(obj, key, value)


# ---------------------------------------------------------------- teachers


@router.get("/teachers", response_model=Page[TeacherOut])
async def list_teachers(
    user: CurrentStaff, session: DbSession, paging: PageParams, include_inactive: bool = False
) -> Page[TeacherOut]:
    """Teachers of the centre, alphabetically."""
    stmt = select(Teacher).where(Teacher.center_id == user.center_id)
    if not include_inactive:
        stmt = stmt.where(Teacher.is_active.is_(True))
    total = await _count(session, stmt)
    rows = (
        await session.execute(stmt.order_by(Teacher.full_name).offset(paging.offset).limit(paging.page_size))
    ).scalars()
    return Page(
        items=[TeacherOut.model_validate(t) for t in rows], total=total, page=paging.page, page_size=paging.page_size
    )


@router.post("/teachers", response_model=TeacherOut, status_code=status.HTTP_201_CREATED)
async def create_teacher(body: TeacherCreate, user: ManagerStaff, session: DbSession) -> TeacherOut:
    """Create a teacher profile (owner / methodist)."""
    teacher = Teacher(center_id=user.center_id, **body.model_dump())
    session.add(teacher)
    await session.flush()
    await audit(
        session,
        center_id=user.center_id,
        actor_type=ActorType.staff,
        actor_id=user.id,
        action="teacher_created",
        entity="teacher",
        entity_id=teacher.id,
    )
    await session.commit()
    return TeacherOut.model_validate(teacher)


@router.get("/teachers/{teacher_id}", response_model=TeacherOut)
async def get_teacher(teacher_id: uuid.UUID, user: CurrentStaff, session: DbSession) -> TeacherOut:
    """One teacher of the centre."""
    return TeacherOut.model_validate(await get_teacher_or_404(session, user, teacher_id))


@router.patch("/teachers/{teacher_id}", response_model=TeacherOut)
async def update_teacher(
    teacher_id: uuid.UUID, body: TeacherUpdate, user: ManagerStaff, session: DbSession
) -> TeacherOut:
    """Partially update a teacher."""
    teacher = await get_teacher_or_404(session, user, teacher_id)
    _apply_updates(teacher, body.model_dump(exclude_unset=True))
    await session.commit()
    return TeacherOut.model_validate(teacher)


@router.delete("/teachers/{teacher_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_teacher(teacher_id: uuid.UUID, user: ManagerStaff, session: DbSession) -> None:
    """Delete a teacher; teachers with lessons are deactivated instead (lessons keep history)."""
    teacher = await get_teacher_or_404(session, user, teacher_id)
    has_lessons = (await session.execute(select(Lesson.id).where(Lesson.teacher_id == teacher.id).limit(1))).first()
    if has_lessons:
        teacher.is_active = False
    else:
        await session.delete(teacher)
    await audit(
        session,
        center_id=user.center_id,
        actor_type=ActorType.staff,
        actor_id=user.id,
        action="teacher_deleted",
        entity="teacher",
        entity_id=teacher.id,
    )
    await session.commit()


# ---------------------------------------------------------------- students


@router.get("/students", response_model=Page[StudentOut])
async def list_students(
    user: CurrentStaff, session: DbSession, paging: PageParams, include_inactive: bool = False
) -> Page[StudentOut]:
    """Students of the centre, alphabetically."""
    stmt = select(Student).where(Student.center_id == user.center_id)
    if not include_inactive:
        stmt = stmt.where(Student.is_active.is_(True))
    total = await _count(session, stmt)
    rows = (
        await session.execute(stmt.order_by(Student.full_name).offset(paging.offset).limit(paging.page_size))
    ).scalars()
    return Page(
        items=[StudentOut.model_validate(s) for s in rows], total=total, page=paging.page, page_size=paging.page_size
    )


@router.post("/students", response_model=StudentOut, status_code=status.HTTP_201_CREATED)
async def create_student(body: StudentCreate, user: ManagerStaff, session: DbSession) -> StudentOut:
    """Create a student (owner / methodist)."""
    student = Student(center_id=user.center_id, **body.model_dump())
    session.add(student)
    await session.flush()
    await audit(
        session,
        center_id=user.center_id,
        actor_type=ActorType.staff,
        actor_id=user.id,
        action="student_created",
        entity="student",
        entity_id=student.id,
    )
    await session.commit()
    return StudentOut.model_validate(student)


@router.get("/students/{student_id}", response_model=StudentOut)
async def get_student(student_id: uuid.UUID, user: CurrentStaff, session: DbSession) -> StudentOut:
    """One student of the centre."""
    return StudentOut.model_validate(await get_student_or_404(session, user, student_id))


@router.patch("/students/{student_id}", response_model=StudentOut)
async def update_student(
    student_id: uuid.UUID, body: StudentUpdate, user: ManagerStaff, session: DbSession
) -> StudentOut:
    """Partially update a student."""
    student = await get_student_or_404(session, user, student_id)
    _apply_updates(student, body.model_dump(exclude_unset=True))
    await session.commit()
    return StudentOut.model_validate(student)


@router.delete("/students/{student_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_student(student_id: uuid.UUID, user: ManagerStaff, session: DbSession) -> None:
    """Delete a student; students with lessons are deactivated instead."""
    student = await get_student_or_404(session, user, student_id)
    has_lessons = (await session.execute(select(Lesson.id).where(Lesson.student_id == student.id).limit(1))).first()
    if has_lessons:
        student.is_active = False
    else:
        await session.delete(student)
    await audit(
        session,
        center_id=user.center_id,
        actor_type=ActorType.staff,
        actor_id=user.id,
        action="student_deleted",
        entity="student",
        entity_id=student.id,
    )
    await session.commit()


# ---------------------------------------------------------------- parents


@router.get("/parents", response_model=Page[ParentOut])
async def list_parents(
    user: ManagerStaff, session: DbSession, paging: PageParams, student_id: uuid.UUID | None = None
) -> Page[ParentOut]:
    """Parents of the centre with linked students (owner / methodist)."""
    stmt = select(Parent).where(Parent.center_id == user.center_id)
    if student_id is not None:
        stmt = stmt.where(Parent.student_links.any(ParentStudent.student_id == student_id))
    total = await _count(session, stmt)
    rows = (
        await session.execute(
            stmt.options(selectinload(Parent.student_links).selectinload(ParentStudent.student))
            .order_by(Parent.full_name)
            .offset(paging.offset)
            .limit(paging.page_size)
        )
    ).scalars()
    return Page(items=[parent_to_dto(p) for p in rows], total=total, page=paging.page, page_size=paging.page_size)


async def _unique_invite_code(session: AsyncSession) -> str:
    try:
        return await issue_unique_invite_code(session)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Не удалось создать код приглашения"
        ) from exc


@router.post("/parents", response_model=ParentOut, status_code=status.HTTP_201_CREATED)
async def create_parent(body: ParentCreate, user: ManagerStaff, session: DbSession) -> ParentOut:
    """Create a parent and optionally link students in one call."""
    data = body.model_dump(exclude={"student_ids", "relation"})
    parent = Parent(center_id=user.center_id, invite_code=await _unique_invite_code(session), **data)
    session.add(parent)
    await session.flush()
    for sid in dict.fromkeys(body.student_ids):
        student = await get_student_or_404(session, user, sid)
        session.add(ParentStudent(parent_id=parent.id, student_id=student.id, relation=body.relation))
    await session.flush()
    await audit(
        session,
        center_id=user.center_id,
        actor_type=ActorType.staff,
        actor_id=user.id,
        action="parent_created",
        entity="parent",
        entity_id=parent.id,
    )
    await session.commit()
    return parent_to_dto(await get_parent_or_404(session, user, parent.id))


@router.get("/parents/{parent_id}", response_model=ParentOut)
async def get_parent(parent_id: uuid.UUID, user: ManagerStaff, session: DbSession) -> ParentOut:
    """One parent with linked students."""
    return parent_to_dto(await get_parent_or_404(session, user, parent_id))


@router.patch("/parents/{parent_id}", response_model=ParentOut)
async def update_parent(parent_id: uuid.UUID, body: ParentUpdate, user: ManagerStaff, session: DbSession) -> ParentOut:
    """Partially update a parent."""
    parent = await get_parent_or_404(session, user, parent_id)
    _apply_updates(parent, body.model_dump(exclude_unset=True))
    await session.commit()
    return parent_to_dto(await get_parent_or_404(session, user, parent.id))


@router.delete("/parents/{parent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_parent(parent_id: uuid.UUID, user: ManagerStaff, session: DbSession) -> None:
    """Delete a parent (links, magic links cascade; notifications keep a null parent_id)."""
    parent = await get_parent_or_404(session, user, parent_id)
    await session.delete(parent)
    await audit(
        session,
        center_id=user.center_id,
        actor_type=ActorType.staff,
        actor_id=user.id,
        action="parent_deleted",
        entity="parent",
        entity_id=parent_id,
    )
    await session.commit()


@router.post("/parents/{parent_id}/link-student", response_model=ParentOut)
async def link_student(
    parent_id: uuid.UUID, body: LinkStudentRequest, user: ManagerStaff, session: DbSession
) -> ParentOut:
    """Link a student of the same centre to the parent (idempotent: updates ``relation`` if linked)."""
    parent = await get_parent_or_404(session, user, parent_id)
    student = await get_student_or_404(session, user, body.student_id)
    existing = next((link for link in parent.student_links if link.student_id == student.id), None)
    if existing is not None:
        existing.relation = body.relation or existing.relation
    else:
        session.add(ParentStudent(parent_id=parent.id, student_id=student.id, relation=body.relation))
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Связь уже существует") from exc
    return parent_to_dto(await get_parent_or_404(session, user, parent.id))


@router.delete("/parents/{parent_id}/link-student/{student_id}", response_model=ParentOut)
async def unlink_student(
    parent_id: uuid.UUID, student_id: uuid.UUID, user: ManagerStaff, session: DbSession
) -> ParentOut:
    """Remove a parent↔student link."""
    parent = await get_parent_or_404(session, user, parent_id)
    for link in list(parent.student_links):
        if link.student_id == student_id:
            await session.delete(link)
    await session.commit()
    return parent_to_dto(await get_parent_or_404(session, user, parent.id))


@router.post("/parents/{parent_id}/revoke-access", response_model=ParentOut)
async def revoke_access(parent_id: uuid.UUID, user: ManagerStaff, session: DbSession) -> ParentOut:
    """«Отозвать доступ»: expire magic links, invalidate issued parent tokens, unbind Telegram, rotate the code.

    Use when a phone is lost, a link was forwarded or the wrong Telegram account was bound. The parent is
    re-invited afterwards with the new invite code / a fresh magic link.
    """
    parent = await get_parent_or_404(session, user, parent_id)
    await revoke_parent_access(session, parent)
    await audit(
        session,
        center_id=user.center_id,
        actor_type=ActorType.staff,
        actor_id=user.id,
        action="parent_access_revoked",
        entity="parent",
        entity_id=parent.id,
    )
    await session.commit()
    log.info("parent_access_revoked", parent_id=str(parent.id), staff_id=str(user.id))
    return parent_to_dto(await get_parent_or_404(session, user, parent.id))


@router.post("/parents/{parent_id}/magic-link", response_model=MagicLinkOut)
async def issue_magic_link(parent_id: uuid.UUID, user: ManagerStaff, session: DbSession) -> MagicLinkOut:
    """Create a fresh portal magic link for manual delivery."""
    parent = await get_parent_or_404(session, user, parent_id)
    url = await create_magic_link(session, parent)
    await audit(
        session,
        center_id=user.center_id,
        actor_type=ActorType.staff,
        actor_id=user.id,
        action="magic_link_issued",
        entity="parent",
        entity_id=parent.id,
    )
    await session.commit()
    return MagicLinkOut(url=url)


@router.post("/parents/{parent_id}/send-invite", response_model=NotificationOut)
async def send_invite(parent_id: uuid.UUID, user: ManagerStaff, session: DbSession) -> NotificationOut:
    """Send the magic link + Telegram deep link via the parent's preferred channel (module C)."""
    parent = await get_parent_or_404(session, user, parent_id)
    try:
        from app.services.notifications import send_parent_invite
    except ImportError as exc:
        log.warning("notifications_module_missing", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Модуль уведомлений недоступен"
        ) from exc
    notification = await send_parent_invite(session, parent.id)
    await audit(
        session,
        center_id=user.center_id,
        actor_type=ActorType.staff,
        actor_id=user.id,
        action="parent_invite_sent",
        entity="parent",
        entity_id=parent.id,
        meta={"status": notification.status.value, "channel": notification.channel.value},
    )
    await session.commit()
    out = NotificationOut.model_validate(notification)
    portal_url = getattr(notification, "portal_url", None)
    if portal_url:  # returned to the manager for manual forwarding, never persisted (see notifications._deliver)
        out.payload = {**out.payload, "magic_url": portal_url}
    return out
