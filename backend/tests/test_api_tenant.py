"""Tenant isolation and role scoping."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Lesson, LessonStatus, MediaKind
from tests.conftest import CenterFactory


async def test_center_b_cannot_see_center_a_student(
    client: AsyncClient, center_factory: CenterFactory, make_student, make_teacher
) -> None:
    a = await center_factory(name="Центр А")
    b = await center_factory(name="Центр Б")
    student = await make_student(a.headers)
    teacher = await make_teacher(a.headers)

    assert (await client.get(f"/api/students/{student['id']}", headers=a.headers)).status_code == 200
    assert (await client.get(f"/api/students/{student['id']}", headers=b.headers)).status_code == 404
    assert (await client.get(f"/api/teachers/{teacher['id']}", headers=b.headers)).status_code == 404
    assert (
        await client.patch(f"/api/students/{student['id']}", json={"grade": 6}, headers=b.headers)
    ).status_code == 404
    assert (await client.delete(f"/api/students/{student['id']}", headers=b.headers)).status_code == 404

    listing = await client.get("/api/students", headers=b.headers)
    assert listing.status_code == 200
    assert listing.json() == {"items": [], "total": 0, "page": 1, "page_size": 20}


async def test_center_b_cannot_link_center_a_student(
    client: AsyncClient, center_factory: CenterFactory, make_student, make_parent
) -> None:
    a = await center_factory(name="Центр А")
    b = await center_factory(name="Центр Б")
    student_a = await make_student(a.headers)
    student_b = await make_student(b.headers)
    parent_b = await make_parent(b.headers, [student_b["id"]])
    resp = await client.post(
        f"/api/parents/{parent_b['id']}/link-student", json={"student_id": student_a["id"]}, headers=b.headers
    )
    assert resp.status_code == 404


async def _insert_lesson(session: AsyncSession, center_id: uuid.UUID, teacher_id: str, student_id: str) -> Lesson:
    lesson = Lesson(
        center_id=center_id,
        teacher_id=uuid.UUID(teacher_id),
        student_id=uuid.UUID(student_id),
        scheduled_at=datetime.now(UTC),
        media_kind=MediaKind.audio,
        status=LessonStatus.done,
        consent_confirmed=True,
        source_filename="source.wav",
    )
    session.add(lesson)
    await session.commit()
    return lesson


async def test_teacher_sees_only_own_lessons(
    client: AsyncClient,
    center_factory: CenterFactory,
    db_session: AsyncSession,
    make_student,
    make_teacher,
    make_teacher_staff,
) -> None:
    ctx = await center_factory()
    student = await make_student(ctx.headers)
    teacher_1 = await make_teacher(ctx.headers, "Первый Преподаватель")
    teacher_2 = await make_teacher(ctx.headers, "Второй Преподаватель")
    own = await _insert_lesson(db_session, ctx.center_id, teacher_1["id"], student["id"])
    other = await _insert_lesson(db_session, ctx.center_id, teacher_2["id"], student["id"])
    _, token = await make_teacher_staff(ctx.center_id, uuid.UUID(teacher_1["id"]))
    headers = {"Authorization": f"Bearer {token}"}

    owner_list = await client.get("/api/lessons", headers=ctx.headers)
    assert owner_list.json()["total"] == 2

    listing = await client.get("/api/lessons", headers=headers)
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()["items"]] == [str(own.id)]
    assert listing.json()["items"][0]["teacher_name"] == "Первый Преподаватель"
    assert listing.json()["items"][0]["stage_index"] == 8
    assert listing.json()["items"][0]["stage_total"] == 8

    assert (await client.get(f"/api/lessons/{own.id}", headers=headers)).status_code == 200
    assert (await client.get(f"/api/lessons/{other.id}", headers=headers)).status_code == 404
    assert (await client.get(f"/api/lessons/{other.id}/reports", headers=headers)).status_code == 404
    assert (await client.get(f"/api/lessons/{own.id}/reports/manager", headers=headers)).status_code == 403

    dashboard = await client.get("/api/dashboard", headers=headers)
    assert dashboard.status_code == 200
    assert dashboard.json()["lessons_total"] == 1


async def test_teacher_cannot_manage_directory(
    client: AsyncClient, center_factory: CenterFactory, make_teacher, make_teacher_staff
) -> None:
    ctx = await center_factory()
    teacher = await make_teacher(ctx.headers)
    _, token = await make_teacher_staff(ctx.center_id, uuid.UUID(teacher["id"]))
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.post("/api/students", json={"full_name": "Новый Ученик"}, headers=headers)).status_code == 403
    assert (await client.get("/api/parents", headers=headers)).status_code == 403
    assert (await client.get("/api/students", headers=headers)).status_code == 200
    assert (await client.get("/api/centers/me", headers=headers)).status_code == 200


async def test_pagination_bounds(client: AsyncClient, center_factory: CenterFactory, make_student) -> None:
    ctx = await center_factory()
    for i in range(3):
        await make_student(ctx.headers, full_name=f"Ученик {i}")
    page = await client.get("/api/students", params={"page": 2, "page_size": 2}, headers=ctx.headers)
    assert page.status_code == 200
    body = page.json()
    assert body["total"] == 3 and body["page"] == 2 and len(body["items"]) == 1
    assert (await client.get("/api/students", params={"page_size": 101}, headers=ctx.headers)).status_code == 422
