"""Shared helpers for module C tests (notifications service, bot handlers, webhook).

Works both under the shared ``tests/conftest.py`` and standalone: the test database URL is taken from
``DATABASE_URL`` (default ``lessonlens_test``), the schema is created with ``Base.metadata.create_all``
(``checkfirst``), and every helper-created centre is deleted afterwards (cascades remove the rest).
This module contains no tests.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings

TEST_DB_URL = (
    os.environ.get("DATABASE_URL") or "postgresql+asyncpg://lessonlens:lessonlens@127.0.0.1:5432/lessonlens_test"
)

if get_settings().database_url != TEST_DB_URL:  # standalone run without conftest: point the app at the test DB
    os.environ["DATABASE_URL"] = TEST_DB_URL
    os.environ.setdefault("APP_ENV", "test")
    os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")
    get_settings.cache_clear()
    from app.db import session as _db_session

    _db_session.get_engine.cache_clear()
    _db_session.get_sessionmaker.cache_clear()

from app.db import models  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_engine, get_sessionmaker  # noqa: E402

BOT_USERNAME = "lessonlens_test_bot"
FAKE_BOT_TOKEN = "123456789:AAE-test-token-not-real-0123456789abcd"

PARENT_REPORT_DATA = {
    "kind": "parent",
    "subject": "Русский язык",
    "topic": "Падежные окончания имён существительных",
    "topic_plain": "Учились правильно менять окончания слов в предложении.",
    "date": "11 октября 2026",
    "child_first_name": "Айдана",
    "what_child_did_well": ["Сама подобрала окончание в слове «книге»", "Прочитала текст без ошибок"],
    "what_was_hard": ["Путала окончания -е и -и в предложном падеже"],
    "what_to_repeat": ["Повторить вопросы падежей (о ком? о чём?)", "Составить 5 предложений со словом «школа»"],
    "home_task_suggestion": "Выписать из учебника 5 слов в предложном падеже",
    "encouragement": "Айдана старалась и не сдавалась — это самое важное.",
    "progress_note": None,
}


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _module_c_schema() -> None:
    """Create missing tables on the session loop (no-op when alembic already did)."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)


@dataclass(slots=True)
class Seed:
    """Ids and codes created by :func:`seed` for one test."""

    center_id: uuid.UUID
    teacher_id: uuid.UUID
    student_id: uuid.UUID
    student2_id: uuid.UUID
    lesson_id: uuid.UUID
    report_id: uuid.UUID
    parent_tg_id: uuid.UUID  # telegram, chat bound (777001)
    parent_tg_unbound_id: uuid.UUID  # telegram, no chat yet
    parent_wa_id: uuid.UUID  # whatsapp with phone
    parent_none_id: uuid.UUID  # notifications disabled
    code_tg: str
    code_tg_unbound: str
    chat_id: int = 777001


def _code(prefix: str) -> str:
    return (prefix + uuid.uuid4().hex)[:8].upper().replace("0", "A").replace("1", "B")


async def seed(session: AsyncSession) -> Seed:
    """Create a centre with a teacher, two students, four parents, one done lesson and a parent report."""
    center = models.Center(name="Тестовый центр C", city="Алматы")
    session.add(center)
    await session.flush()
    teacher = models.Teacher(center_id=center.id, full_name="Айгуль Сериковна", subjects=["russian"])
    student = models.Student(center_id=center.id, full_name="Айдана Сериккызы", grade=5)
    student2 = models.Student(center_id=center.id, full_name="Арман Болатов", grade=6)
    session.add_all([teacher, student, student2])
    await session.flush()
    code_tg, code_tg_unbound = _code("TG"), _code("UN")
    parent_tg = models.Parent(
        center_id=center.id,
        full_name="Гульнара Сериковна",
        preferred_channel=models.Channel.telegram,
        telegram_chat_id=777001,
        invite_code=code_tg,
    )
    parent_unbound = models.Parent(
        center_id=center.id,
        full_name="Болат Ержанович",
        preferred_channel=models.Channel.telegram,
        invite_code=code_tg_unbound,
    )
    parent_wa = models.Parent(
        center_id=center.id,
        full_name="Сауле Маратовна",
        preferred_channel=models.Channel.whatsapp,
        phone="+7 701 000 00 01",
        invite_code=_code("WA"),
    )
    parent_none = models.Parent(
        center_id=center.id,
        full_name="Данияр Асанович",
        preferred_channel=models.Channel.none,
        invite_code=_code("NO"),
    )
    session.add_all([parent_tg, parent_unbound, parent_wa, parent_none])
    await session.flush()
    session.add_all(
        [
            models.ParentStudent(parent_id=parent_tg.id, student_id=student.id, relation="мама"),
            models.ParentStudent(parent_id=parent_tg.id, student_id=student2.id, relation="мама"),
            models.ParentStudent(parent_id=parent_unbound.id, student_id=student.id, relation="папа"),
            models.ParentStudent(parent_id=parent_wa.id, student_id=student.id, relation="тётя"),
            models.ParentStudent(parent_id=parent_none.id, student_id=student.id, relation="опекун"),
        ]
    )
    lesson = models.Lesson(
        center_id=center.id,
        teacher_id=teacher.id,
        student_id=student.id,
        subject="russian",
        scheduled_at=datetime(2026, 10, 11, 10, 0, tzinfo=UTC),
        status=models.LessonStatus.done,
        consent_confirmed=True,
        media_kind=models.MediaKind.video,
    )
    session.add(lesson)
    await session.flush()
    report = models.Report(
        lesson_id=lesson.id,
        kind=models.ReportKind.parent,
        language=models.Language.ru,
        data={**PARENT_REPORT_DATA, "lesson_id": str(lesson.id), "student_id": str(student.id)},
        markdown="# Русский язык",
        telegram_html="<b>Айдана — русский язык</b>",
        version=1,
        published_at=datetime.now(UTC),
    )
    session.add(report)
    await session.commit()
    return Seed(
        center_id=center.id,
        teacher_id=teacher.id,
        student_id=student.id,
        student2_id=student2.id,
        lesson_id=lesson.id,
        report_id=report.id,
        parent_tg_id=parent_tg.id,
        parent_tg_unbound_id=parent_unbound.id,
        parent_wa_id=parent_wa.id,
        parent_none_id=parent_none.id,
        code_tg=code_tg,
        code_tg_unbound=code_tg_unbound,
    )


async def cleanup(center_id: uuid.UUID) -> None:
    """Delete the seeded centre; FK cascades remove everything that hangs off it."""
    async with get_sessionmaker()() as session:
        await session.execute(delete(models.Center).where(models.Center.id == center_id))
        await session.commit()


@pytest_asyncio.fixture
async def seeded() -> AsyncIterator[Seed]:
    """Seeded data for one test, removed afterwards."""
    async with get_sessionmaker()() as session:
        data = await seed(session)
    try:
        yield data
    finally:
        await cleanup(data.center_id)


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """A fresh AsyncSession on the test database."""
    async with get_sessionmaker()() as s:
        yield s


@pytest.fixture
def mock_messaging(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """Route every channel to the in-memory mock messenger and enable the (fake) Telegram bot for deep links."""
    from app.bot import telegram as bot_module
    from app.providers.messaging import mock

    monkeypatch.setenv("MESSAGING_PROVIDER", "mock")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_BOT_TOKEN)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", BOT_USERNAME)
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")
    get_settings.cache_clear()
    mock.reset()
    bot_module.set_bot_username(None)
    yield
    mock.reset()
    get_settings.cache_clear()


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    """The app's sessionmaker (shared with handlers under test)."""
    return get_sessionmaker()
