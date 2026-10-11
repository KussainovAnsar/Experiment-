"""Shared pytest fixtures: test database (lessonlens_test), ASGI client, auth helpers, factories.

The test DB URL is forced through environment variables BEFORE ``app`` is imported so that
``Settings`` (and the cached engine) point at ``lessonlens_test``. Schema is created once per
session with ``alembic upgrade head``; all tables are truncated after every test.
Other modules' tests reuse these fixtures — keep them generic.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import pytest_asyncio

BACKEND_DIR = Path(__file__).resolve().parent.parent
TEST_DB_ASYNC = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://lessonlens:lessonlens@127.0.0.1:5432/lessonlens_test"
)
TEST_DB_SYNC = TEST_DB_ASYNC.replace("postgresql+asyncpg://", "postgresql+psycopg://")
_MEDIA_TMP = tempfile.mkdtemp(prefix="lessonlens-test-media-")

os.environ["DATABASE_URL"] = TEST_DB_ASYNC
os.environ["DATABASE_URL_SYNC"] = TEST_DB_SYNC
os.environ["APP_ENV"] = "test"
os.environ["MEDIA_ROOT"] = _MEDIA_TMP
os.environ["PIPELINE_MODE"] = "inline"
os.environ["STT_PROVIDER"] = "mock"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production-0123456789")
os.environ["PUBLIC_BASE_URL"] = "http://testserver"

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import models  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_engine, get_sessionmaker  # noqa: E402
from app.services.auth import create_staff_token, hash_password  # noqa: E402

get_settings.cache_clear()
assert get_settings().database_url.endswith("lessonlens_test"), "tests must run against lessonlens_test"


# ---------------------------------------------------------------- schema lifecycle


def _alembic_upgrade_head() -> None:
    env = {**os.environ, "DATABASE_URL_SYNC": TEST_DB_SYNC}
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(BACKEND_DIR / "alembic.ini"), "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="session", autouse=True)
def _schema() -> None:
    """Create the schema once per test session (alembic upgrade head)."""
    _alembic_upgrade_head()


@pytest_asyncio.fixture(autouse=True)
async def _truncate_tables() -> AsyncIterator[None]:
    """Truncate every table after each test so tests stay independent."""
    yield
    table_names = [t.name for t in Base.metadata.sorted_tables]
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE " + ", ".join(f'"{n}"' for n in table_names) + " CASCADE"))


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _dispose_engine() -> AsyncIterator[None]:
    yield
    await get_engine().dispose()


# ---------------------------------------------------------------- app / db fixtures


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """httpx client speaking ASGI directly to the FastAPI app."""
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """A plain AsyncSession on the test database (commit explicitly)."""
    async with get_sessionmaker()() as session:
        yield session


@pytest.fixture
def media_root() -> Path:
    """MEDIA_ROOT used by the test process."""
    return get_settings().media_root


# ---------------------------------------------------------------- factories


@dataclass(slots=True)
class CenterCtx:
    """A registered centre with its owner token."""

    center_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    password: str
    token: str
    name: str
    extra: dict = field(default_factory=dict)

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


CenterFactory = Callable[..., Awaitable[CenterCtx]]


@pytest_asyncio.fixture
async def center_factory(client: AsyncClient) -> CenterFactory:
    """``await center_factory(name="Центр А")`` registers a centre through the API and returns its owner context."""

    async def _make(name: str = "Тестовый центр", email: str | None = None, password: str = "password123") -> CenterCtx:
        email = email or f"owner-{uuid.uuid4().hex[:8]}@example.com"
        resp = await client.post(
            "/api/auth/register-center",
            json={
                "center_name": name,
                "city": "Алматы",
                "owner_name": "Владелец",
                "email": email,
                "password": password,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        return CenterCtx(
            center_id=uuid.UUID(body["user"]["center_id"]),
            user_id=uuid.UUID(body["user"]["id"]),
            email=email,
            password=password,
            token=body["access_token"],
            name=name,
        )

    return _make


@pytest_asyncio.fixture
async def center(center_factory: CenterFactory) -> CenterCtx:
    """Default centre for a test."""
    return await center_factory(name="Центр А")


@pytest.fixture
def staff_headers(center: CenterCtx) -> dict[str, str]:
    """Authorization headers of the default centre's owner."""
    return center.headers


@pytest_asyncio.fixture
async def make_teacher(client: AsyncClient) -> Callable[..., Awaitable[dict]]:
    async def _make(headers: dict[str, str], full_name: str = "Айгуль Сериковна") -> dict:
        resp = await client.post(
            "/api/teachers", json={"full_name": full_name, "subjects": ["russian"]}, headers=headers
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    return _make


@pytest_asyncio.fixture
async def make_student(client: AsyncClient) -> Callable[..., Awaitable[dict]]:
    async def _make(headers: dict[str, str], full_name: str = "Ученик Тестовый", grade: int = 5) -> dict:
        resp = await client.post("/api/students", json={"full_name": full_name, "grade": grade}, headers=headers)
        assert resp.status_code == 201, resp.text
        return resp.json()

    return _make


@pytest_asyncio.fixture
async def make_parent(client: AsyncClient) -> Callable[..., Awaitable[dict]]:
    async def _make(headers: dict[str, str], student_ids: list[str], full_name: str = "Родитель Тестовый") -> dict:
        resp = await client.post(
            "/api/parents",
            json={"full_name": full_name, "preferred_channel": "none", "student_ids": student_ids, "relation": "мама"},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    return _make


@pytest_asyncio.fixture
async def make_teacher_staff(db_session: AsyncSession) -> Callable[..., Awaitable[tuple[models.StaffUser, str]]]:
    """Create a ``teacher``-role staff user linked to a teacher profile; returns (user, bearer token)."""

    async def _make(
        center_id: uuid.UUID, teacher_id: uuid.UUID, email: str | None = None
    ) -> tuple[models.StaffUser, str]:
        user = models.StaffUser(
            center_id=center_id,
            email=email or f"teacher-{uuid.uuid4().hex[:8]}@example.com",
            password_hash=hash_password("password123"),
            full_name="Преподаватель",
            role=models.StaffRole.teacher,
            teacher_id=teacher_id,
        )
        db_session.add(user)
        await db_session.commit()
        return user, create_staff_token(user)

    return _make


@pytest.fixture
def no_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the pipeline hand-off with a no-op so uploads do not start background processing."""
    import app.api.lessons as lessons_api

    async def _noop(session, lesson_id) -> None:  # noqa: ANN001
        return None

    monkeypatch.setattr(lessons_api, "_enqueue", _noop)


@pytest.fixture(scope="session")
def tiny_wav(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One second of silence as 16 kHz mono wav, generated with ffmpeg."""
    path = tmp_path_factory.mktemp("media") / "silence.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "1", str(path)],
        check=True,
        capture_output=True,
    )
    assert path.stat().st_size > 0
    return path
