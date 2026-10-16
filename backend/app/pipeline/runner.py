"""Pipeline orchestrator: ``run_lesson_pipeline`` runs the stages of docs/CONTRACT.md §4.1 and
``enqueue_lesson_processing`` hands a lesson to it (inline background task or ``jobs`` row).

Every stage opens its own ``AsyncSession`` from the sessionmaker: the status is written and committed before
the work starts, artefacts are committed when the stage ends, so partial progress survives a crash and the
frontend can poll ``GET /api/lessons/{id}`` for progress. Any exception marks the lesson ``failed`` with a
Russian ``status_message`` and the truncated technical ``error`` (file-system paths are redacted); nothing
propagates to the caller.

A run starts by *claiming* the lesson (``app.pipeline.locking.claim_lesson``, a compare-and-set on
``lessons.status``): a second concurrent run — double click, duplicate job, retry — gets ``status=None`` back and
touches nothing. Inline-mode background tasks are tracked so the API can finish or cancel them on shutdown and
sweep lessons that a dead process left in an intermediate status (``sweep_interrupted_lessons``).

Module B2 (``analysis``/``reports``/``progress``) and module C (``notifications``) are imported lazily at
stage time; tests may override them through :data:`HOOK_OVERRIDES`.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import inspect
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.config import REPO_DIR, Settings, get_settings
from app.db.models import Frame, FrameReason, Lesson, LessonStatus, MediaKind, Transcript
from app.db.session import get_sessionmaker
from app.logging import get_logger
from app.pipeline import jobs, locking
from app.pipeline.media import (
    ExtractedFrame,
    MediaError,
    MediaInfo,
    extract_audio,
    extract_compact_audio,
    extract_keyframes,
    probe_media,
)
from app.pipeline.media import spread_indices as _spread_indices
from app.providers.stt.base import ProviderNotConfigured, STTError, TranscriptResult
from app.providers.stt.registry import get_stt_provider

log = get_logger("app.pipeline.runner")

INLINE_START_DELAY_SEC = 0.2
INLINE_SHUTDOWN_TIMEOUT_SEC = 10.0
_ERROR_MAX_CHARS = 2000
_STATUS_MESSAGE_MAX = 500
CANCELLED_MESSAGE = "Обработка прервана остановкой сервера — запустите обработку заново"

# Failures that will not go away on a retry (bad file, invalid model output, DB constraint, missing module).
# The worker does not re-run these; transient STT/LLM/network errors stay retryable.
NON_RETRYABLE_ERROR_TYPES: frozenset[str] = frozenset(
    {
        "MediaError",
        "ValidationError",
        "AnalysisInputError",
        "PipelineDependencyMissing",
        "ProviderNotConfigured",
        "DBAPIError",
        "IntegrityError",
        "DataError",
        "ProgrammingError",
        "LessonMissing",
    }
)


@dataclass(slots=True)
class PipelineResult:
    """Outcome of one run: ``status`` is ``None`` when the lesson was already owned by another run (no-op)."""

    status: LessonStatus | None
    error_type: str | None = None
    retryable: bool = False
    # Free-tier revolver ran out of quota: re-run no earlier than this (the worker overrides its backoff).
    retry_after_sec: float | None = None

# Human-readable (Russian) progress messages per stage.
STAGE_MESSAGES: dict[LessonStatus, str] = {
    LessonStatus.probing: "Читаем метаданные записи",
    LessonStatus.extracting_audio: "Извлекаем аудиодорожку",
    LessonStatus.transcribing: "Распознаём речь (кадры извлекаются параллельно)",
    LessonStatus.extracting_frames: "Извлекаем ключевые кадры",
    LessonStatus.analyzing: "Анализируем урок",
    LessonStatus.reporting: "Формируем отчёты",
    LessonStatus.notifying: "Отправляем уведомления родителям",
    LessonStatus.done: "Обработка завершена",
}

# Lazily-resolved cross-module functions: hook name -> (module, attribute). Tests may put callables into
# HOOK_OVERRIDES to bypass the import (e.g. when module B2/C are absent).
HOOKS: dict[str, tuple[str, str]] = {
    "analyze_lesson": ("app.pipeline.analysis", "analyze_lesson"),
    "build_reports": ("app.pipeline.reports", "build_reports"),
    "update_progress": ("app.pipeline.progress", "update_progress"),
    "notify_parents_for_lesson": ("app.services.notifications", "notify_parents_for_lesson"),
}
HOOK_OVERRIDES: dict[str, Callable[..., Awaitable[Any]] | None] = {}

_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


class PipelineDependencyMissing(RuntimeError):
    """A sibling module needed by a stage is not importable."""


def _resolve_hook(name: str) -> Callable[..., Awaitable[Any]]:
    override = HOOK_OVERRIDES.get(name)
    if override is not None:
        return override
    module_name, attr = HOOKS[name]
    try:
        module = importlib.import_module(module_name)
        return getattr(module, attr)
    except (ImportError, AttributeError) as exc:
        raise PipelineDependencyMissing(f"{module_name}.{attr} is unavailable: {exc}") from exc


# ---------------------------------------------------------------- storage paths (module A, with fallback)


def _paths(settings: Settings, center_id: uuid.UUID, lesson_id: uuid.UUID) -> tuple[Path, Path, Path]:
    """Return ``(lesson_dir, audio_path, frames_dir)`` using module A's storage helpers or an identical fallback."""
    try:
        from app.services.storage import audio_path, frames_dir, lesson_dir

        return lesson_dir(center_id, lesson_id), audio_path(center_id, lesson_id), frames_dir(center_id, lesson_id)
    except ImportError:
        base = settings.media_root / str(center_id) / str(lesson_id)
        frames = base / "frames"
        frames.mkdir(parents=True, exist_ok=True)
        return base, base / "audio.wav", frames


def _wants_compact_audio(provider: Any) -> bool:
    """Cloud providers upload the (copied) source audio track; local Whisper and chunking providers read the WAV."""
    return bool(getattr(provider, "prefers_compact_audio", False))


# ---------------------------------------------------------------- public API


async def enqueue_lesson_processing(session: AsyncSession, lesson_id: uuid.UUID) -> None:
    """Hand a lesson to the pipeline.

    ``PIPELINE_MODE=inline``: schedule :func:`run_lesson_pipeline` as a background task on the running loop;
    the task sleeps briefly so the caller's commit lands, then opens its own sessions.
    ``PIPELINE_MODE=worker``: insert a ``jobs`` row (``kind=process_lesson``); the caller commits it.
    """
    settings = get_settings()
    if settings.pipeline_mode == "worker":
        if await jobs.has_active_job(session, jobs.KIND_PROCESS_LESSON, lesson_id=lesson_id):
            log.info("lesson_job_already_queued", lesson_id=str(lesson_id))
            return
        await jobs.enqueue(session, jobs.KIND_PROCESS_LESSON, {"lesson_id": str(lesson_id)})
        log.info("lesson_job_enqueued", lesson_id=str(lesson_id))
        return
    loop = asyncio.get_running_loop()
    task = loop.create_task(_run_inline(lesson_id), name=f"lesson-pipeline-{lesson_id}")
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    log.info("lesson_pipeline_scheduled_inline", lesson_id=str(lesson_id))


async def _run_inline(lesson_id: uuid.UUID) -> None:
    await asyncio.sleep(INLINE_START_DELAY_SEC)
    await run_lesson_pipeline(lesson_id)


async def shutdown_inline_tasks(timeout: float = INLINE_SHUTDOWN_TIMEOUT_SEC) -> int:
    """Give running inline pipelines ``timeout`` seconds to finish, then cancel the rest (they mark their lesson
    ``failed`` with :data:`CANCELLED_MESSAGE`). Returns the number of cancelled tasks."""
    pending = [t for t in _BACKGROUND_TASKS if not t.done()]
    if not pending:
        return 0
    log.info("inline_tasks_draining", count=len(pending), timeout_sec=timeout)
    _, still_running = await asyncio.wait(pending, timeout=timeout)
    for task in still_running:
        task.cancel()
    if still_running:
        await asyncio.gather(*still_running, return_exceptions=True)
        log.warning("inline_tasks_cancelled", count=len(still_running))
    return len(still_running)


async def sweep_interrupted_lessons(sessionmaker: async_sessionmaker | None = None) -> int:
    """Inline mode, at startup: lessons stuck in an intermediate status belong to a dead process → ``failed``."""
    sm = sessionmaker or get_sessionmaker()
    async with sm() as session:
        return await locking.sweep_interrupted_lessons(session)


async def run_lesson_pipeline(lesson_id: uuid.UUID, *, sessionmaker: async_sessionmaker | None = None) -> None:
    """Run every stage for ``lesson_id``; never raises (failures end in ``status=failed``)."""
    await run_lesson_pipeline_detailed(lesson_id, sessionmaker=sessionmaker)


async def run_lesson_pipeline_result(
    lesson_id: uuid.UUID, *, sessionmaker: async_sessionmaker | None = None
) -> LessonStatus | None:
    """Final status (``done`` / ``failed``), or ``None`` when another run already owns the lesson."""
    return (await run_lesson_pipeline_detailed(lesson_id, sessionmaker=sessionmaker)).status


async def run_lesson_pipeline_detailed(
    lesson_id: uuid.UUID, *, sessionmaker: async_sessionmaker | None = None
) -> PipelineResult:
    """Run the pipeline and report status, error type and whether a retry makes sense (for the worker)."""
    sm = sessionmaker or get_sessionmaker()
    settings = get_settings()
    structlog.contextvars.bind_contextvars(lesson_id=str(lesson_id))
    started = time.monotonic()
    timings: dict[str, int] = {}
    info: MediaInfo | None = None
    current_stage: LessonStatus = LessonStatus.probing
    try:
        lesson = await _load_lesson(sm, lesson_id)
        if lesson is None:
            log.warning("pipeline_lesson_missing")
            return PipelineResult(LessonStatus.failed, "LessonMissing", False)
        async with sm() as session:
            claimed = await locking.claim_lesson(
                session, lesson_id, status_message=STAGE_MESSAGES[LessonStatus.probing]
            )
        if not claimed:
            log.info("pipeline_skipped_already_running")
            return PipelineResult(None)
        center_id = lesson.center_id
        source = Path(lesson.source_path or "")
        language_hints = list(lesson.language_hints or ["ru", "kk"])
        _, audio_dst, frames_out = _paths(settings, center_id, lesson_id)

        current_stage = LessonStatus.probing
        info = await _timed(current_stage, _stage_probe(sm, lesson_id, source, settings), timings)

        # Two independent branches run concurrently: (audio → speech recognition) and (keyframes). STT is by far
        # the longest stage, so the frames are ready long before the transcript and add no wall-clock time.
        frames_task = asyncio.create_task(
            _timed(
                LessonStatus.extracting_frames,
                _stage_extract_frames(sm, lesson_id, source, frames_out, info, settings, set_status=False),
                timings,
            ),
            name=f"lesson-frames-{lesson_id}",
        )
        try:
            stt = get_stt_provider(settings)
            current_stage = LessonStatus.extracting_audio
            audio = await _timed(
                current_stage, _stage_extract_audio(sm, lesson_id, source, audio_dst, info, stt, settings), timings
            )

            current_stage = LessonStatus.transcribing
            await _timed(
                current_stage,
                _with_heartbeat(
                    sm, lesson_id, current_stage, _stage_transcribe(sm, lesson_id, audio, language_hints, stt)
                ),
                timings,
            )
        except BaseException:
            # Let the frames branch finish (it cannot be interrupted mid-ffmpeg) so no late write lands after
            # the lesson is marked failed; its own error, if any, is secondary.
            with contextlib.suppress(BaseException):
                await frames_task
            raise

        current_stage = LessonStatus.extracting_frames
        if not frames_task.done():
            await _set_status(sm, lesson_id, current_stage, STAGE_MESSAGES[current_stage])
        await frames_task

        current_stage = LessonStatus.analyzing
        analyze = _with_heartbeat(sm, lesson_id, current_stage, _stage_analyze(sm, lesson_id))
        await _timed(current_stage, analyze, timings)

        current_stage = LessonStatus.reporting
        await _timed(current_stage, _stage_report(sm, lesson_id), timings)

        current_stage = LessonStatus.notifying
        await _timed(current_stage, _stage_notify(sm, lesson_id), timings)

        stats = _processing_stats(timings, started, info, settings)
        await _set_status(
            sm, lesson_id, LessonStatus.done, STAGE_MESSAGES[LessonStatus.done], done=True, processing_stats=stats
        )
        log.info("pipeline_done", total_ms=int((time.monotonic() - started) * 1000), speed_x=stats.get("speed_x"))
        return PipelineResult(LessonStatus.done)
    except asyncio.CancelledError:
        log.warning("pipeline_cancelled", stage=current_stage.value)
        try:
            await asyncio.shield(_mark_failed(sm, lesson_id, current_stage, _Cancelled()))
        except Exception:  # pragma: no cover - last resort, DB unavailable
            log.exception("pipeline_mark_failed_error")
        raise
    except Exception as exc:
        error_type = type(exc).__name__
        log.exception("pipeline_failed", stage=current_stage.value, error_type=error_type)
        try:
            await _mark_failed(
                sm, lesson_id, current_stage, exc, processing_stats=_processing_stats(timings, started, info, settings)
            )
        except Exception:  # pragma: no cover - last resort, DB unavailable
            log.exception("pipeline_mark_failed_error")
        return PipelineResult(LessonStatus.failed, error_type, is_retryable(exc), _retry_after_sec(exc))
    finally:
        structlog.contextvars.unbind_contextvars("lesson_id")


class _Cancelled(RuntimeError):
    """Marker exception used to record a cancellation (server stop) in ``lessons.error``."""

    def __str__(self) -> str:
        return "processing task cancelled on server shutdown"


def _retry_after_sec(exc: BaseException) -> float | None:
    """``retry_after_sec`` of a revolver ``FreeLimitsExhausted`` (LLM or STT), else ``None``."""
    value = getattr(exc, "retry_after_sec", None)
    return float(value) if isinstance(value, int | float) and value > 0 else None


def is_retryable(exc: BaseException) -> bool:
    """False for deterministic failures (see :data:`NON_RETRYABLE_ERROR_TYPES`), True otherwise."""
    return not any(cls.__name__ in NON_RETRYABLE_ERROR_TYPES for cls in type(exc).__mro__)


# ---------------------------------------------------------------- stage plumbing


async def _timed(stage: LessonStatus, coro: Awaitable[Any], timings: dict[str, int] | None = None) -> Any:
    t0 = time.monotonic()
    log.info("stage_start", stage=stage.value)
    result = await coro
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    if timings is not None:
        timings[stage.value] = elapsed_ms
    log.info("stage_end", stage=stage.value, elapsed_ms=elapsed_ms)
    return result


def _heartbeat_interval() -> float:
    try:
        return max(1.0, float(os.environ.get("PIPELINE_HEARTBEAT_SEC", "20")))
    except ValueError:
        return 20.0


def _elapsed_ru(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60} мин {seconds % 60:02d} с" if seconds >= 60 else f"{seconds} с"


async def _with_heartbeat(
    sm: async_sessionmaker, lesson_id: uuid.UUID, stage: LessonStatus, coro: Awaitable[Any]
) -> Any:
    """Await ``coro`` while refreshing ``status_message`` (and thus ``updated_at``) every few seconds.

    The centre sees that a long stage (speech recognition of a 90-minute lesson) is alive — «Распознаём речь ·
    3 мин 20 с» — and the stale-claim guard (``locking``, 30 min of no updates) never mistakes a slow but
    healthy run for an abandoned one.
    """
    started = time.monotonic()
    base = STAGE_MESSAGES.get(stage, stage.value)

    async def beat() -> None:
        while True:
            await asyncio.sleep(_heartbeat_interval())
            message = f"{base} · {_elapsed_ru(time.monotonic() - started)}"
            with contextlib.suppress(Exception):
                async with sm() as session:
                    await session.execute(
                        update(Lesson)
                        .where(Lesson.id == lesson_id, Lesson.status == stage)
                        .values(status_message=message[:_STATUS_MESSAGE_MAX])
                    )
                    await session.commit()

    task = asyncio.create_task(beat(), name=f"lesson-heartbeat-{lesson_id}")
    try:
        return await coro
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _processing_stats(
    timings: dict[str, int], started: float, info: MediaInfo | None, settings: Settings
) -> dict[str, Any]:
    """Per-stage wall-clock times stored on the lesson (``lessons.processing_stats``) for the UI and tuning."""
    total_sec = round(time.monotonic() - started, 1)
    stats: dict[str, Any] = {
        "total_sec": total_sec,
        "stages_ms": dict(timings),
        "stt_provider": settings.stt_provider,
        "llm_provider": settings.llm_provider,
        "parallel_frames": True,
    }
    if info is not None and info.duration_sec:
        stats["media_duration_sec"] = info.duration_sec
        stats["speed_x"] = round(info.duration_sec / total_sec, 1) if total_sec > 0 else None
    return stats


async def _load_lesson(sm: async_sessionmaker, lesson_id: uuid.UUID) -> Lesson | None:
    async with sm() as session:
        return await session.get(Lesson, lesson_id)


async def _set_status(
    sm: async_sessionmaker,
    lesson_id: uuid.UUID,
    status: LessonStatus,
    message: str,
    *,
    done: bool = False,
    processing_stats: dict[str, Any] | None = None,
) -> None:
    """Write ``status``/``status_message`` (and ``processed_at`` when ``done``) in a dedicated transaction."""
    async with sm() as session:
        lesson = await session.get(Lesson, lesson_id)
        if lesson is None:
            raise RuntimeError(f"lesson {lesson_id} disappeared during processing")
        lesson.status = status
        lesson.status_message = message[:_STATUS_MESSAGE_MAX]
        if status != LessonStatus.failed:
            lesson.error = None
        if done:
            lesson.processed_at = datetime.now(UTC)
        if processing_stats is not None:
            lesson.processing_stats = processing_stats
        await session.commit()


def redact_paths(text: str) -> str:
    """Hide the server's directory layout (MEDIA_ROOT, repository path) from user-facing error text."""
    settings = get_settings()
    for raw, label in ((str(settings.media_root), "<MEDIA_ROOT>"), (str(REPO_DIR), "<APP>")):
        if raw and raw != "/" and raw in text:
            text = text.replace(raw, label)
    return text


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def _failure_message(stage: LessonStatus, exc: BaseException) -> str:
    stage_ru = STAGE_MESSAGES.get(stage, stage.value).lower()
    if isinstance(exc, _Cancelled):
        return CANCELLED_MESSAGE
    if _retry_after_sec(exc) is not None:  # free-tier revolver: limits exhausted, the message says so
        return redact_paths(str(exc))
    if isinstance(exc, ProviderNotConfigured):
        return f"Провайдер распознавания речи не настроен: {exc}"
    if isinstance(exc, STTError):
        return f"Ошибка распознавания речи: {redact_paths(str(exc))}"
    if isinstance(exc, MediaError):
        # Only the first stderr line: ffmpeg/ffprobe output is technical and echoes absolute paths.
        return f"Не удалось обработать медиафайл (этап: {stage_ru}): {redact_paths(_first_line(str(exc)))}"
    if isinstance(exc, PipelineDependencyMissing):
        return f"Модуль обработки недоступен (этап: {stage_ru}): {exc}"
    if type(exc).__name__ == "ValidationError":
        return f"Ответ модели анализа не прошёл валидацию (этап: {stage_ru})"
    if isinstance(exc, DBAPIError):
        return f"Ошибка базы данных на этапе «{stage_ru}» — обратитесь к администратору (подробности в поле error)"
    return f"Ошибка на этапе «{stage_ru}»: {type(exc).__name__}: {redact_paths(str(exc))}"


async def _mark_failed(
    sm: async_sessionmaker,
    lesson_id: uuid.UUID,
    stage: LessonStatus,
    exc: BaseException,
    *,
    processing_stats: dict[str, Any] | None = None,
) -> None:
    async with sm() as session:
        lesson = await session.get(Lesson, lesson_id)
        if lesson is None:
            return
        lesson.status = LessonStatus.failed
        lesson.status_message = _failure_message(stage, exc)[:_STATUS_MESSAGE_MAX]
        lesson.error = redact_paths(f"[{stage.value}] {type(exc).__name__}: {exc}")[:_ERROR_MAX_CHARS]
        if processing_stats is not None:
            lesson.processing_stats = processing_stats
        await session.commit()


# ---------------------------------------------------------------- stages


async def _stage_probe(sm: async_sessionmaker, lesson_id: uuid.UUID, source: Path, settings: Settings) -> MediaInfo:
    # status=probing was already written by the claim (locking.claim_lesson)
    if not source.is_file():
        raise MediaError(f"source file not found: {source}")
    info = await asyncio.to_thread(probe_media, source, ffprobe_bin=settings.ffprobe_bin)
    if not info.has_audio:
        raise MediaError("the recording has no audio track")
    async with sm() as session:
        lesson = await session.get(Lesson, lesson_id)
        if lesson is None:
            raise RuntimeError("lesson disappeared")
        lesson.duration_sec = info.duration_sec
        lesson.media_kind = MediaKind.video if info.has_video else MediaKind.audio
        await session.commit()
    log.info(
        "probed",
        duration_sec=info.duration_sec,
        has_video=info.has_video,
        width=info.width,
        height=info.height,
        video_codec=info.video_codec,
        audio_codec=info.audio_codec,
    )
    return info


async def _stage_extract_audio(
    sm: async_sessionmaker,
    lesson_id: uuid.UUID,
    source: Path,
    audio_dst: Path,
    info: MediaInfo,
    provider: Any,
    settings: Settings,
) -> Path:
    """Prepare the provider's input: the copied source audio track for cloud STT, a 16 kHz WAV otherwise."""
    await _set_status(sm, lesson_id, LessonStatus.extracting_audio, STAGE_MESSAGES[LessonStatus.extracting_audio])
    if _wants_compact_audio(provider):
        audio = await asyncio.to_thread(
            extract_compact_audio,
            source,
            audio_dst.parent,
            audio_codec=info.audio_codec,
            stem=audio_dst.stem,
            ffmpeg_bin=settings.ffmpeg_bin,
        )
    else:
        audio = await asyncio.to_thread(extract_audio, source, audio_dst, ffmpeg_bin=settings.ffmpeg_bin)
    async with sm() as session:
        lesson = await session.get(Lesson, lesson_id)
        if lesson is None:
            raise RuntimeError("lesson disappeared")
        lesson.audio_path = str(audio)
        await session.commit()
    log.info("audio_ready", file=audio.name, size_bytes=audio.stat().st_size, codec=info.audio_codec)
    return audio


async def _stage_transcribe(
    sm: async_sessionmaker, lesson_id: uuid.UUID, audio: Path, language_hints: list[str], provider: Any
) -> TranscriptResult:
    await _set_status(sm, lesson_id, LessonStatus.transcribing, STAGE_MESSAGES[LessonStatus.transcribing])
    result = provider.transcribe(audio, language_hints=language_hints)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, TranscriptResult):
        raise STTError(f"provider {provider.name!r} returned {type(result).__name__}, expected TranscriptResult")
    async with sm() as session:
        await session.execute(delete(Transcript).where(Transcript.lesson_id == lesson_id))
        session.add(
            Transcript(
                lesson_id=lesson_id,
                provider=result.provider,
                language=result.language,
                segments=result.segments_as_dicts(),
                full_text=result.full_text,
                speaker_map=dict(result.speaker_map or {}),
                word_count=result.word_count,
                duration_sec=result.duration_sec,
            )
        )
        await session.commit()
    log.info(
        "transcribed",
        provider=result.provider,
        language=result.language,
        segments=len(result.segments),
        words=result.word_count,
        diarized=bool(result.speaker_map),
    )
    return result


async def _stage_extract_frames(
    sm: async_sessionmaker,
    lesson_id: uuid.UUID,
    source: Path,
    frames_out: Path,
    info: MediaInfo,
    settings: Settings,
    *,
    set_status: bool = True,
) -> list[ExtractedFrame]:
    if not info.has_video:
        # Audio-only: no frames stage, but drop frames of a previous (video) run just in case.
        async with sm() as session:
            await session.execute(delete(Frame).where(Frame.lesson_id == lesson_id))
            await session.commit()
        log.info("frames_skipped_audio_only")
        return []
    if set_status:
        await _set_status(
            sm, lesson_id, LessonStatus.extracting_frames, STAGE_MESSAGES[LessonStatus.extracting_frames]
        )
    frames = await asyncio.to_thread(
        extract_keyframes,
        source,
        frames_out,
        duration_sec=info.duration_sec,
        interval_sec=settings.keyframe_interval_sec,
        scene_threshold=settings.keyframe_scene_threshold,
        max_frames=settings.keyframe_max,
        ffmpeg_bin=settings.ffmpeg_bin,
        ffprobe_bin=settings.ffprobe_bin,
        has_video=info.has_video,
    )
    selected = set(_spread_indices(len(frames), settings.llm_max_frames))
    async with sm() as session:
        await session.execute(delete(Frame).where(Frame.lesson_id == lesson_id))
        for f in frames:
            session.add(
                Frame(
                    lesson_id=lesson_id,
                    index=f.index,
                    timestamp_sec=f.timestamp_sec,
                    path=str(f.path),
                    width=f.width,
                    height=f.height,
                    phash=f.phash,
                    reason=FrameReason(f.reason),
                    selected_for_llm=f.index in selected,
                )
            )
        await session.commit()
    log.info("frames_saved", count=len(frames), selected_for_llm=len(selected))
    return frames


async def _stage_analyze(sm: async_sessionmaker, lesson_id: uuid.UUID) -> None:
    await _set_status(sm, lesson_id, LessonStatus.analyzing, STAGE_MESSAGES[LessonStatus.analyzing])
    analyze_lesson = _resolve_hook("analyze_lesson")
    async with sm() as session:
        lesson = (
            await session.execute(
                select(Lesson)
                .options(selectinload(Lesson.transcript), selectinload(Lesson.frames), selectinload(Lesson.student))
                .where(Lesson.id == lesson_id)
            )
        ).scalar_one()
        analysis = await analyze_lesson(session, lesson)
        await session.commit()
        log.info("analyzed", provider=getattr(analysis, "provider", None), model=getattr(analysis, "model", None))


async def _stage_report(sm: async_sessionmaker, lesson_id: uuid.UUID) -> None:
    await _set_status(sm, lesson_id, LessonStatus.reporting, STAGE_MESSAGES[LessonStatus.reporting])
    build_reports = _resolve_hook("build_reports")
    update_progress = _resolve_hook("update_progress")
    async with sm() as session:
        lesson = (
            await session.execute(
                select(Lesson)
                .options(
                    selectinload(Lesson.analysis),
                    selectinload(Lesson.transcript),
                    selectinload(Lesson.frames),
                    selectinload(Lesson.student),
                    selectinload(Lesson.teacher),
                )
                .where(Lesson.id == lesson_id)
            )
        ).scalar_one()
        if lesson.analysis is None:
            raise RuntimeError("analysis row missing after the analyzing stage")
        reports = await build_reports(session, lesson, lesson.analysis)
        await session.flush()
        skills = await update_progress(session, lesson, lesson.analysis)
        await session.commit()
        log.info("reported", reports=len(reports or []), skills=len(skills or []))


async def _stage_notify(sm: async_sessionmaker, lesson_id: uuid.UUID) -> None:
    """Send parent notifications; failures are logged and never fail the lesson."""
    await _set_status(sm, lesson_id, LessonStatus.notifying, STAGE_MESSAGES[LessonStatus.notifying])
    try:
        notify = _resolve_hook("notify_parents_for_lesson")
    except PipelineDependencyMissing as exc:
        log.warning("notify_skipped_module_missing", error=str(exc))
        return
    try:
        async with sm() as session:
            notifications = await notify(session, lesson_id)
            await session.commit()
            log.info("notified", count=len(notifications or []))
    except Exception as exc:
        log.exception("notify_failed", error_type=type(exc).__name__, error=str(exc)[:300])
