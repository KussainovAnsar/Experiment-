"""Job queue against the test database (standalone: own engine, ``create_all(checkfirst=True)``)."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Job, JobStatus
from app.pipeline import jobs

TEST_DB_URL = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://lessonlens:lessonlens@127.0.0.1:5432/lessonlens_test"
)


@pytest.fixture(scope="module")
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(TEST_DB_URL, pool_pre_ping=True)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="module")
def sm(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def kind(sm: async_sessionmaker[AsyncSession]) -> AsyncIterator[str]:
    """A unique job kind per test so concurrent tests never see each other's rows; cleans up afterwards."""
    k = f"test_{uuid.uuid4().hex[:10]}"
    yield k
    async with sm() as session:
        await session.execute(delete(Job).where(Job.kind == k))
        await session.commit()


def test_backoff_is_exponential_and_capped() -> None:
    assert [jobs.backoff_seconds(n) for n in (1, 2, 3, 4)] == [30, 60, 120, 240]
    assert jobs.backoff_seconds(0) == 30
    assert jobs.backoff_seconds(20) == jobs.BACKOFF_MAX_SEC


async def test_enqueue_claim_complete(sm: async_sessionmaker[AsyncSession], kind: str) -> None:
    lesson_id = str(uuid.uuid4())
    async with sm() as session:
        job = await jobs.enqueue(session, kind, {"lesson_id": lesson_id})
        await session.commit()
        assert job.status == JobStatus.queued and job.attempts == 0

    async with sm() as s1, sm() as s2:
        claimed = await jobs.claim_next(s1, "worker-a", kinds=[kind])
        assert claimed is not None and claimed.id == job.id
        assert claimed.status == JobStatus.running
        assert claimed.attempts == 1
        assert claimed.locked_by == "worker-a" and claimed.locked_at is not None
        # Already running → nothing else to claim for this kind.
        assert await jobs.claim_next(s2, "worker-b", kinds=[kind]) is None
        await jobs.complete(s1, claimed)

    async with sm() as session:
        fresh = (await session.execute(select(Job).where(Job.id == job.id))).scalar_one()
        assert fresh.status == JobStatus.done
        assert fresh.locked_by is None and fresh.error is None


async def test_fail_retries_with_backoff_then_fails(sm: async_sessionmaker[AsyncSession], kind: str) -> None:
    async with sm() as session:
        await jobs.enqueue(session, kind, {"lesson_id": "x"}, max_attempts=2)
        await session.commit()

    async with sm() as session:
        claimed = await jobs.claim_next(session, "w", kinds=[kind])
        assert claimed is not None
        before = datetime.now(UTC)
        await jobs.fail(session, claimed, "boom " * 1000)
        assert claimed.status == JobStatus.queued
        assert claimed.error is not None and len(claimed.error) <= 2000
        assert claimed.run_after >= before + timedelta(seconds=jobs.backoff_seconds(1) - 1)
        # Not due yet → not claimable.
        assert await jobs.claim_next(session, "w", kinds=[kind]) is None
        # Make it due and claim again (attempt 2 of 2) → terminal failure.
        claimed.run_after = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
        again = await jobs.claim_next(session, "w", kinds=[kind])
        assert again is not None and again.attempts == 2
        await jobs.fail(session, again, "boom again")
        assert again.status == JobStatus.failed


async def test_fail_without_retry_is_terminal(sm: async_sessionmaker[AsyncSession], kind: str) -> None:
    async with sm() as session:
        await jobs.enqueue(session, kind, {})
        await session.commit()
        claimed = await jobs.claim_next(session, "w", kinds=[kind])
        assert claimed is not None
        await jobs.fail(session, claimed, "unknown kind", retry=False)
        assert claimed.status == JobStatus.failed and claimed.attempts == 1


async def test_claim_respects_run_after_order(sm: async_sessionmaker[AsyncSession], kind: str) -> None:
    now = datetime.now(UTC)
    async with sm() as session:
        later = await jobs.enqueue(session, kind, {"n": 2}, run_after=now - timedelta(seconds=10))
        earlier = await jobs.enqueue(session, kind, {"n": 1}, run_after=now - timedelta(seconds=20))
        await session.commit()
        first = await jobs.claim_next(session, "w", kinds=[kind])
        second = await jobs.claim_next(session, "w", kinds=[kind])
        assert first is not None and second is not None
        assert first.id == earlier.id and second.id == later.id


async def test_release_stale(sm: async_sessionmaker[AsyncSession], kind: str) -> None:
    async with sm() as session:
        await jobs.enqueue(session, kind, {})
        await session.commit()
        claimed = await jobs.claim_next(session, "dead-worker", kinds=[kind])
        assert claimed is not None
        claimed.locked_at = datetime.now(UTC) - timedelta(hours=5)
        await session.commit()
        assert await jobs.release_stale(session, older_than=timedelta(hours=3)) >= 1
        again = await jobs.claim_next(session, "alive", kinds=[kind])
        assert again is not None and again.id == claimed.id and again.attempts == 2
        await jobs.complete(session, again)
