"""Parent portal: magic links, token exchange, student scoping."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Lesson, LessonStatus, MagicLink, MediaKind, Report, ReportKind
from tests.conftest import CenterCtx


async def _setup(client: AsyncClient, center: CenterCtx, make_student, make_parent) -> tuple[dict, dict, dict]:
    student = await make_student(center.headers, "Ребёнок Один")
    other = await make_student(center.headers, "Ребёнок Чужой")
    parent = await make_parent(center.headers, [student["id"]])
    return student, other, parent


async def test_magic_link_exchange_and_scoping(
    client: AsyncClient, center: CenterCtx, db_session: AsyncSession, make_student, make_parent
) -> None:
    student, other, parent = await _setup(client, center, make_student, make_parent)

    link = await client.post(f"/api/parents/{parent['id']}/magic-link", headers=center.headers)
    assert link.status_code == 200
    url = link.json()["url"]
    assert url.startswith("http://testserver/p/")
    token = url.rsplit("/", 1)[1]

    stored = (await db_session.execute(select(MagicLink))).scalar_one()
    assert stored.token_hash != token and len(stored.token_hash) == 64
    assert stored.used_at is None

    exchanged = await client.post("/api/parent/auth/exchange", json={"token": token})
    assert exchanged.status_code == 200, exchanged.text
    body = exchanged.json()
    assert body["parent"]["id"] == parent["id"]
    assert [s["id"] for s in body["students"]] == [student["id"]]
    headers = {"Authorization": f"Bearer {body['access_token']}"}

    # link can be reopened until it expires
    again = await client.post("/api/parent/auth/exchange", json={"token": token})
    assert again.status_code == 200

    me = await client.get("/api/parent/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["center_name"] == center.name

    students = await client.get("/api/parent/students", headers=headers)
    assert students.status_code == 200
    cards = students.json()
    assert len(cards) == 1
    assert cards[0]["student"]["id"] == student["id"]
    assert cards[0]["lessons_count"] == 0
    assert cards[0]["skills_summary"] == {"struggling": 0, "developing": 0, "mastered": 0}

    assert (await client.get(f"/api/parent/students/{student['id']}/lessons", headers=headers)).status_code == 200
    assert (await client.get(f"/api/parent/students/{other['id']}/lessons", headers=headers)).status_code == 404
    assert (await client.get(f"/api/parent/students/{uuid.uuid4()}/lessons", headers=headers)).status_code == 404

    # parent token is not a staff token and vice versa
    assert (await client.get("/api/students", headers=headers)).status_code == 401
    assert (await client.get("/api/parent/me", headers=center.headers)).status_code == 401

    patched = await client.patch("/api/parent/me", json={"preferred_language": "kk"}, headers=headers)
    assert patched.status_code == 200 and patched.json()["preferred_language"] == "kk"


async def test_exchange_rejects_bad_or_expired_token(
    client: AsyncClient, center: CenterCtx, db_session: AsyncSession, make_student, make_parent
) -> None:
    _, _, parent = await _setup(client, center, make_student, make_parent)
    assert (await client.post("/api/parent/auth/exchange", json={"token": "x" * 43})).status_code == 401

    link = await client.post(f"/api/parents/{parent['id']}/magic-link", headers=center.headers)
    token = link.json()["url"].rsplit("/", 1)[1]
    stored = (await db_session.execute(select(MagicLink))).scalar_one()
    stored.expires_at = datetime(2000, 1, 1, tzinfo=UTC)
    await db_session.commit()
    assert (await client.post("/api/parent/auth/exchange", json={"token": token})).status_code == 401


async def test_parent_lesson_cards_and_report(
    client: AsyncClient, center: CenterCtx, db_session: AsyncSession, make_student, make_parent, make_teacher
) -> None:
    student, _, parent = await _setup(client, center, make_student, make_parent)
    teacher = await make_teacher(center.headers)
    lesson = Lesson(
        center_id=center.center_id,
        teacher_id=uuid.UUID(teacher["id"]),
        student_id=uuid.UUID(student["id"]),
        scheduled_at=datetime.now(UTC),
        media_kind=MediaKind.video,
        status=LessonStatus.done,
        consent_confirmed=True,
    )
    db_session.add(lesson)
    await db_session.flush()
    data = {
        "topic_plain": "Падежи",
        "what_child_did_well": ["Сам нашёл окончание"],
        "what_was_hard": [],
        "what_to_repeat": ["Окончания -е / -и", "Ударение"],
        "encouragement": "Отличная работа!",
    }
    db_session.add(Report(lesson_id=lesson.id, kind=ReportKind.parent, data={"topic_plain": "старое"}, version=1))
    db_session.add(Report(lesson_id=lesson.id, kind=ReportKind.parent, data=data, version=2))
    await db_session.commit()

    link = await client.post(f"/api/parents/{parent['id']}/magic-link", headers=center.headers)
    token = link.json()["url"].rsplit("/", 1)[1]
    access = (await client.post("/api/parent/auth/exchange", json={"token": token})).json()["access_token"]
    headers = {"Authorization": f"Bearer {access}"}

    cards = (await client.get(f"/api/parent/students/{student['id']}/lessons", headers=headers)).json()
    assert cards == [
        {
            "lesson_id": str(lesson.id),
            "scheduled_at": cards[0]["scheduled_at"],
            "topic_plain": "Падежи",
            "encouragement": "Отличная работа!",
            "what_to_repeat_count": 2,
        }
    ]
    detail = await client.get(f"/api/parent/students/{student['id']}/lessons/{lesson.id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["data"] == data
    assert detail.json()["teacher_name"] == teacher["full_name"]
    assert detail.json()["frames"] == []

    students = (await client.get("/api/parent/students", headers=headers)).json()
    assert students[0]["lessons_count"] == 1 and students[0]["last_lesson_at"] is not None
