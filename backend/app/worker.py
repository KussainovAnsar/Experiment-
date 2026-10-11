"""Job worker: ``python -m app.worker`` (``PIPELINE_MODE=worker``).

Polls the ``jobs`` table every 2 s, claims one job at a time with ``FOR UPDATE SKIP LOCKED`` and runs it.
``process_lesson`` jobs run :func:`app.pipeline.runner.run_lesson_pipeline_detailed`; a lesson that ends in
``failed`` fails the job, which is retried with exponential backoff up to ``max_attempts`` **only for transient
errors** (STT/LLM/network) — deterministic failures (bad media, invalid model output, DB constraint, missing
module) fail the job at once instead of re-running the lesson three times. A job whose lesson is already being
processed by another run is completed as a no-op. SIGTERM/SIGINT stop the loop after the current job finishes
(graceful shutdown). Stale ``running`` jobs (worker crashed) are released back to the queue every few minutes.

Environment: ``WORKER_POLL_INTERVAL_SEC`` (2), ``WORKER_ID`` (hostname-pid), ``WORKER_STALE_JOB_HOURS`` (3).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.db.models import Job, LessonStatus
from app.db.session import get_engine, get_sessionmaker
from app.logging import configure_logging, get_logger
from app.pipeline import jobs
from app.pipeline.runner import run_lesson_pipeline_detailed
from app.startup_checks import check_secret_key

log = get_logger("app.worker")

DEFAULT_POLL_INTERVAL_SEC = 2.0
_STALE_CHECK_EVERY_SEC = 300.0


def default_worker_id() -> str:
    """``<hostname>-<pid>`` unless ``WORKER_ID`` is set."""
    return os.environ.get("WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}"


async def handle_job(job: Job, *, sessionmaker: async_sessionmaker) -> tuple[bool, str | None, bool]:
    """Execute one claimed job. Returns ``(ok, error, retryable)``."""
    if job.kind == jobs.KIND_PROCESS_LESSON:
        raw = (job.payload or {}).get("lesson_id")
        try:
            lesson_id = uuid.UUID(str(raw))
        except (ValueError, TypeError):
            return False, f"invalid lesson_id in payload: {raw!r}", False
        result = await run_lesson_pipeline_detailed(lesson_id, sessionmaker=sessionmaker)
        if result.status is None:
            log.info("job_skipped_lesson_busy", job_id=str(job.id), lesson_id=str(lesson_id))
            return True, None, False  # another run owns the lesson: nothing to do, nothing to retry
        if result.status == LessonStatus.done:
            return True, None, False
        return (
            False,
            f"lesson {lesson_id} ended with status {result.status.value} ({result.error_type})",
            result.retryable,
        )
    return False, f"unknown job kind: {job.kind}", False


async def run_once(*, worker_id: str, sessionmaker: async_sessionmaker | None = None) -> bool:
    """Claim and run a single job. Returns ``True`` when a job was processed, ``False`` when the queue was empty."""
    sm = sessionmaker or get_sessionmaker()
    async with sm() as session:
        job = await jobs.claim_next(session, worker_id)
        if job is None:
            return False
        try:
            ok, error, retryable = await handle_job(job, sessionmaker=sm)
        except Exception as exc:  # the pipeline never raises, but guard the loop anyway
            log.exception("job_crashed", job_id=str(job.id))
            ok, error, retryable = False, f"{type(exc).__name__}: {exc}", True
        if ok:
            await jobs.complete(session, job)
        else:
            await jobs.fail(session, job, error or "unknown error", retry=retryable)
        return True


async def run_worker(
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SEC,
    worker_id: str | None = None,
    stop_event: asyncio.Event | None = None,
    sessionmaker: async_sessionmaker | None = None,
) -> None:
    """Main loop: process jobs until ``stop_event`` is set."""
    worker_id = worker_id or default_worker_id()
    stop_event = stop_event or asyncio.Event()
    sm = sessionmaker or get_sessionmaker()
    stale_hours = float(os.environ.get("WORKER_STALE_JOB_HOURS", "3"))
    log.info("worker_start", worker_id=worker_id, poll_interval=poll_interval)
    last_stale_check = -_STALE_CHECK_EVERY_SEC
    loop = asyncio.get_running_loop()
    while not stop_event.is_set():
        now = loop.time()
        if now - last_stale_check >= _STALE_CHECK_EVERY_SEC:
            last_stale_check = now
            with contextlib.suppress(Exception):
                async with sm() as session:
                    await jobs.release_stale(session, older_than=timedelta(hours=stale_hours))
        try:
            processed = await run_once(worker_id=worker_id, sessionmaker=sm)
        except Exception:
            log.exception("worker_iteration_failed")
            processed = False
        if processed:
            continue
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
    log.info("worker_stop", worker_id=worker_id)


async def _main_async() -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop(signame: str) -> None:
        log.info("worker_signal", signal=signame)
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_stop, sig.name)
    poll = float(os.environ.get("WORKER_POLL_INTERVAL_SEC", DEFAULT_POLL_INTERVAL_SEC))
    try:
        await run_worker(poll_interval=poll, stop_event=stop_event)
    finally:
        await get_engine().dispose()


def main() -> None:
    """Entry point for ``python -m app.worker``."""
    settings = get_settings()
    configure_logging(level="DEBUG" if settings.app_env == "dev" else "INFO", json_output=settings.app_env != "dev")
    check_secret_key(settings)  # the worker signs nothing, but a prod stack must never run on the public key
    if settings.pipeline_mode != "worker":
        log.warning("worker_running_with_inline_mode", pipeline_mode=settings.pipeline_mode)
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
