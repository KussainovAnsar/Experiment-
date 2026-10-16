"""PostgreSQL-backed job queue (``jobs`` table): enqueue / claim (``FOR UPDATE SKIP LOCKED``) / complete / fail.

Semantics:

* :func:`enqueue` only ``flush``es — the caller owns the transaction (API handlers commit after the insert).
* :func:`claim_next`, :func:`complete`, :func:`fail` and :func:`release_stale` ``commit`` — they are worker-side
  operations and the row lock taken by ``FOR UPDATE SKIP LOCKED`` must be released promptly.
* Retries: ``fail`` re-queues the job with exponential ``run_after`` backoff until ``attempts >= max_attempts``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, JobStatus
from app.logging import get_logger

log = get_logger("app.pipeline.jobs")

KIND_PROCESS_LESSON = "process_lesson"
BACKOFF_BASE_SEC = 30
BACKOFF_MAX_SEC = 3600
_ERROR_MAX_CHARS = 2000


def backoff_seconds(attempts: int) -> int:
    """Exponential backoff for the *next* run after ``attempts`` failed attempts: 30 s, 60 s, 120 s … ≤ 1 h."""
    return int(min(BACKOFF_MAX_SEC, BACKOFF_BASE_SEC * (2 ** max(0, attempts - 1))))


async def enqueue(
    session: AsyncSession,
    kind: str,
    payload: dict,
    *,
    max_attempts: int = 3,
    run_after: datetime | None = None,
) -> Job:
    """Insert a queued job and flush it (no commit)."""
    job = Job(
        kind=kind,
        payload=payload,
        status=JobStatus.queued,
        attempts=0,
        max_attempts=max_attempts,
        run_after=run_after or datetime.now(UTC),
    )
    session.add(job)
    await session.flush()
    log.info("job_enqueued", job_id=str(job.id), kind=kind)
    return job


async def has_active_job(session: AsyncSession, kind: str, *, lesson_id: object) -> bool:
    """True when a ``queued`` / ``running`` job of ``kind`` already carries ``payload.lesson_id`` (dedupe)."""
    stmt = (
        select(Job.id)
        .where(
            Job.kind == kind,
            Job.status.in_([JobStatus.queued, JobStatus.running]),
            Job.payload["lesson_id"].astext == str(lesson_id),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).first() is not None


async def claim_next(session: AsyncSession, worker_id: str, *, kinds: list[str] | None = None) -> Job | None:
    """Atomically claim the oldest due job: mark it ``running`` and commit. ``None`` when nothing is due."""
    stmt = (
        select(Job)
        .where(Job.status == JobStatus.queued, Job.run_after <= func.now())
        .order_by(Job.run_after, Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if kinds:
        stmt = stmt.where(Job.kind.in_(kinds))
    job = (await session.execute(stmt)).scalar_one_or_none()
    if job is None:
        await session.rollback()
        return None
    job.status = JobStatus.running
    job.attempts += 1
    job.locked_at = datetime.now(UTC)
    job.locked_by = worker_id
    await session.commit()
    log.info("job_claimed", job_id=str(job.id), kind=job.kind, attempt=job.attempts, worker_id=worker_id)
    return job


async def complete(session: AsyncSession, job: Job) -> None:
    """Mark the job ``done`` and commit."""
    job.status = JobStatus.done
    job.locked_at = None
    job.locked_by = None
    job.error = None
    await session.commit()
    log.info("job_done", job_id=str(job.id), kind=job.kind, attempts=job.attempts)


async def fail(
    session: AsyncSession, job: Job, error: str, *, retry: bool = True, delay_sec: float | None = None
) -> None:
    """Record the error; re-queue with backoff while attempts remain (and ``retry``), else mark ``failed``.

    ``delay_sec`` overrides the exponential backoff (free-tier limits exhausted: retry when they cool down).
    """
    job.error = (error or "")[:_ERROR_MAX_CHARS]
    job.locked_at = None
    job.locked_by = None
    if retry and job.attempts < job.max_attempts:
        delay = int(min(delay_sec, BACKOFF_MAX_SEC)) if delay_sec else backoff_seconds(job.attempts)
        job.status = JobStatus.queued
        job.run_after = datetime.now(UTC) + timedelta(seconds=delay)
        log.warning("job_retry_scheduled", job_id=str(job.id), kind=job.kind, attempt=job.attempts, delay_sec=delay)
    else:
        job.status = JobStatus.failed
        log.error("job_failed", job_id=str(job.id), kind=job.kind, attempts=job.attempts)
    await session.commit()


async def release_stale(session: AsyncSession, *, older_than: timedelta = timedelta(hours=3)) -> int:
    """Re-queue ``running`` jobs whose worker vanished (``locked_at`` older than ``older_than``). Commits."""
    cutoff = datetime.now(UTC) - older_than
    result = await session.execute(
        update(Job)
        .where(Job.status == JobStatus.running, Job.locked_at.is_not(None), Job.locked_at < cutoff)
        .values(status=JobStatus.queued, locked_at=None, locked_by=None, run_after=func.now())
    )
    await session.commit()
    count = int(result.rowcount or 0)
    if count:
        log.warning("jobs_released_stale", count=count)
    return count
