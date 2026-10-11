"""/api/lessons — upload, list, status, reprocess, transcript, frames, analysis."""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import Select, Text, cast, exists, false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentStaff, DbSession, PageParams, is_teacher, not_found
from app.config import get_settings
from app.db.models import (
    LESSON_STAGE_ORDER,
    ActorType,
    Analysis,
    Frame,
    Lesson,
    LessonStatus,
    MediaKind,
    Report,
    StaffUser,
    Student,
    Teacher,
    Transcript,
)
from app.logging import get_logger
from app.schemas.api import AnalysisOut, FrameOut, LessonOut, Page, TranscriptOut
from app.services.audit import audit
from app.services.storage import lesson_dir, safe_extension, source_path

router = APIRouter(prefix="/api/lessons", tags=["lessons"])
log = get_logger(__name__)

VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".mov", ".webm"})
AUDIO_EXTENSIONS = frozenset({".m4a", ".mp3", ".wav", ".ogg", ".aac"})
ALLOWED_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
UPLOAD_CHUNK_BYTES = 1024 * 1024
ALLOWED_LANGUAGE_HINTS = frozenset({"ru", "kk", "en"})
STAGE_TOTAL = len(LESSON_STAGE_ORDER) - 1

_PIPELINE_UNAVAILABLE = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="Модуль обработки уроков недоступен. Обратитесь к администратору.",
)


# ---------------------------------------------------------------- helpers shared with other routers


def stage_index_for(status_value: LessonStatus) -> int | None:
    """Index of the status in LESSON_STAGE_ORDER; ``None`` for ``failed``."""
    try:
        return LESSON_STAGE_ORDER.index(status_value)
    except ValueError:
        return None


def lesson_rows_query(center_id: uuid.UUID, *, teacher_id: uuid.UUID | None = None) -> Select[Any]:
    """Base SELECT producing ``(Lesson, student_name, teacher_name, has_transcript, has_analysis, kinds)`` rows.

    When ``teacher_id`` is given the query is scoped to that teacher (teacher role).
    """
    has_transcript = exists().where(Transcript.lesson_id == Lesson.id).correlate(Lesson).label("has_transcript")
    has_analysis = exists().where(Analysis.lesson_id == Lesson.id).correlate(Lesson).label("has_analysis")
    report_kinds = (
        select(func.array_agg(func.distinct(cast(Report.kind, Text))))
        .where(Report.lesson_id == Lesson.id)
        .correlate(Lesson)
        .scalar_subquery()
        .label("report_kinds")
    )
    stmt = (
        select(Lesson, Student.full_name, Teacher.full_name, has_transcript, has_analysis, report_kinds)
        .join(Student, Student.id == Lesson.student_id)
        .join(Teacher, Teacher.id == Lesson.teacher_id)
        .where(Lesson.center_id == center_id)
    )
    if teacher_id is not None:
        stmt = stmt.where(Lesson.teacher_id == teacher_id)
    return stmt


def staff_lessons_query(user: StaffUser) -> Select[Any]:
    """Lesson rows visible to ``user``: whole centre for owner/methodist, own lessons for teachers."""
    if is_teacher(user):
        if user.teacher_id is None:
            # A teacher account not linked to a teacher profile sees nothing.
            return lesson_rows_query(user.center_id).where(false())
        return lesson_rows_query(user.center_id, teacher_id=user.teacher_id)
    return lesson_rows_query(user.center_id)


def row_to_lesson_dto(row: Any) -> LessonOut:
    """Convert a ``lesson_rows_query`` row into ``LessonOut``."""
    lesson: Lesson = row[0]
    kinds = row[5] or []
    return LessonOut(
        id=lesson.id,
        student_id=lesson.student_id,
        student_name=row[1],
        teacher_id=lesson.teacher_id,
        teacher_name=row[2],
        subject=lesson.subject,
        scheduled_at=lesson.scheduled_at,
        duration_sec=lesson.duration_sec,
        media_kind=lesson.media_kind,
        source_filename=lesson.source_filename,
        status=lesson.status,
        status_message=lesson.status_message,
        error=lesson.error,
        stage_index=stage_index_for(lesson.status),
        stage_total=STAGE_TOTAL,
        has_transcript=bool(row[3]),
        has_analysis=bool(row[4]),
        report_kinds=sorted(kinds),
        language_hints=list(lesson.language_hints or []),
        consent_confirmed=lesson.consent_confirmed,
        created_at=lesson.created_at,
        processed_at=lesson.processed_at,
    )


async def fetch_lesson_dto(session: AsyncSession, user: StaffUser, lesson_id: uuid.UUID) -> LessonOut:
    """Load one lesson as DTO with tenant/role scoping; 404 when not visible."""
    result = await session.execute(staff_lessons_query(user).where(Lesson.id == lesson_id))
    row = result.first()
    if row is None:
        raise not_found("Урок")
    return row_to_lesson_dto(row)


async def get_lesson_or_404(session: AsyncSession, user: StaffUser, lesson_id: uuid.UUID) -> Lesson:
    """Load a ``Lesson`` ORM object visible to ``user`` (tenant + teacher scoping), else 404."""
    stmt = select(Lesson).where(Lesson.id == lesson_id, Lesson.center_id == user.center_id)
    if is_teacher(user):
        if user.teacher_id is None:
            raise not_found("Урок")
        stmt = stmt.where(Lesson.teacher_id == user.teacher_id)
    lesson = (await session.execute(stmt)).scalar_one_or_none()
    if lesson is None:
        raise not_found("Урок")
    return lesson


async def _enqueue(session: AsyncSession, lesson_id: uuid.UUID) -> None:
    """Hand the lesson to the pipeline (module B1). Lazy import so a missing module yields 503."""
    try:
        from app.pipeline.runner import enqueue_lesson_processing
    except ImportError as exc:
        log.warning("pipeline_runner_missing", error=str(exc))
        raise _PIPELINE_UNAVAILABLE from exc
    await enqueue_lesson_processing(session, lesson_id)


def frame_url(frame_id: uuid.UUID) -> str:
    """Public URL of a keyframe (served by /api/media)."""
    return f"/api/media/frames/{frame_id}"


# ---------------------------------------------------------------- endpoints


@router.get("", response_model=Page[LessonOut])
async def list_lessons(
    user: CurrentStaff,
    session: DbSession,
    paging: PageParams,
    student_id: uuid.UUID | None = None,
    teacher_id: uuid.UUID | None = None,
    status_filter: Annotated[LessonStatus | None, Query(alias="status")] = None,
) -> Page[LessonOut]:
    """List lessons of the centre (teachers: own only), newest first. Filters: student_id, teacher_id, status."""
    base = staff_lessons_query(user)
    if student_id is not None:
        base = base.where(Lesson.student_id == student_id)
    if teacher_id is not None:
        base = base.where(Lesson.teacher_id == teacher_id)
    if status_filter is not None:
        base = base.where(Lesson.status == status_filter)
    total = (await session.execute(select(func.count()).select_from(base.order_by(None).subquery()))).scalar_one()
    rows = await session.execute(
        base.order_by(Lesson.scheduled_at.desc(), Lesson.created_at.desc())
        .offset(paging.offset)
        .limit(paging.page_size)
    )
    return Page(
        items=[row_to_lesson_dto(r) for r in rows.all()], total=total, page=paging.page, page_size=paging.page_size
    )


def _parse_language_hints(raw: str | None) -> list[str]:
    hints = [h.strip().lower() for h in (raw or "").split(",") if h.strip()]
    hints = [h for h in hints if h in ALLOWED_LANGUAGE_HINTS]
    return hints or ["ru", "kk"]


def _parse_bool(raw: str | bool | None) -> bool:
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


async def _stream_upload_to_disk(file: UploadFile, dst: Path, *, max_bytes: int) -> int:
    """Copy the upload to ``dst`` in chunks. Raises 413 (and removes the partial file) when too large."""
    written = 0
    try:
        with dst.open("wb") as out:
            while True:
                chunk = await file.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=f"Файл больше допустимого размера ({max_bytes // (1024 * 1024)} МБ)",
                    )
                out.write(chunk)
    except BaseException:
        dst.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    return written


@router.post("", response_model=LessonOut, status_code=status.HTTP_201_CREATED)
async def upload_lesson(  # noqa: PLR0913 - multipart form fields
    request: Request,
    user: CurrentStaff,
    session: DbSession,
    file: Annotated[UploadFile, File(description="Запись урока: mp4, mkv, mov, webm, m4a, mp3, wav, ogg, aac")],
    student_id: Annotated[uuid.UUID, Form()],
    teacher_id: Annotated[uuid.UUID | None, Form()] = None,
    scheduled_at: Annotated[datetime | None, Form()] = None,
    subject: Annotated[str, Form(max_length=64)] = "russian",
    consent_confirmed: Annotated[str | None, Form()] = None,
    language_hints: Annotated[str | None, Form(description="Через запятую, напр. ru,kk")] = None,
) -> LessonOut:
    """Upload a lesson recording (streamed to disk) and start processing."""
    settings = get_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024

    if not _parse_bool(consent_confirmed):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Необходимо подтвердить согласие на обработку записи урока",
        )
    ext = safe_extension(file.filename or "")
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Недопустимый формат файла. Разрешены: " + ", ".join(sorted(e[1:] for e in ALLOWED_EXTENSIONS)),
        )
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > max_bytes + 64 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Файл больше допустимого размера ({settings.max_upload_mb} МБ)",
        )

    if is_teacher(user):
        if user.teacher_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Учётная запись преподавателя не привязана к профилю"
            )
        if teacher_id is not None and teacher_id != user.teacher_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Можно загружать только свои уроки")
        teacher_id = user.teacher_id
    if teacher_id is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Укажите преподавателя")

    student = await session.get(Student, student_id)
    if student is None or student.center_id != user.center_id:
        raise not_found("Ученик")
    teacher = await session.get(Teacher, teacher_id)
    if teacher is None or teacher.center_id != user.center_id:
        raise not_found("Преподаватель")

    lesson = Lesson(
        center_id=user.center_id,
        teacher_id=teacher.id,
        student_id=student.id,
        subject=(subject or "russian").strip() or "russian",
        scheduled_at=scheduled_at or datetime.now(UTC),
        source_filename=Path(file.filename or f"source{ext}").name[:255],
        media_kind=MediaKind.video if ext in VIDEO_EXTENSIONS else MediaKind.audio,
        status=LessonStatus.uploaded,
        status_message="Файл загружен, ожидает обработки",
        consent_confirmed=True,
        language_hints=_parse_language_hints(language_hints),
    )
    session.add(lesson)
    await session.flush()
    lesson_id, center_id = lesson.id, user.center_id  # keep plain values: rollback() expires ORM attributes

    dst = source_path(center_id, lesson_id, file.filename or f"source{ext}")
    try:
        size = await _stream_upload_to_disk(file, dst, max_bytes=max_bytes)
    except BaseException:
        await session.rollback()
        _cleanup_lesson_dir(center_id, lesson_id)
        raise
    if size == 0:
        await session.rollback()
        _cleanup_lesson_dir(center_id, lesson_id)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Загружен пустой файл")

    lesson.source_path = str(dst)
    await audit(
        session,
        center_id=user.center_id,
        actor_type=ActorType.staff,
        actor_id=user.id,
        action="lesson_uploaded",
        entity="lesson",
        entity_id=lesson.id,
        meta={"size_bytes": size, "media_kind": lesson.media_kind.value},
    )
    await session.commit()
    log.info("lesson_uploaded", lesson_id=str(lesson.id), center_id=str(user.center_id), size_bytes=size)

    await _enqueue(session, lesson.id)
    await session.commit()
    return await fetch_lesson_dto(session, user, lesson.id)


def _cleanup_lesson_dir(center_id: uuid.UUID, lesson_id: uuid.UUID) -> None:
    """Remove the (empty or partial) lesson directory after a failed upload."""
    directory = lesson_dir(center_id, lesson_id)
    for child in directory.glob("*"):
        if child.is_file():
            child.unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        directory.rmdir()


@router.get("/{lesson_id}", response_model=LessonOut)
async def get_lesson(lesson_id: uuid.UUID, user: CurrentStaff, session: DbSession) -> LessonOut:
    """Lesson card with processing status (poll this for progress)."""
    return await fetch_lesson_dto(session, user, lesson_id)


@router.post("/{lesson_id}/reprocess", response_model=LessonOut)
async def reprocess_lesson(lesson_id: uuid.UUID, user: CurrentStaff, session: DbSession) -> LessonOut:
    """Reset a finished or failed lesson to ``uploaded`` and run the pipeline again."""
    lesson = await get_lesson_or_404(session, user, lesson_id)
    if lesson.status not in (LessonStatus.done, LessonStatus.failed, LessonStatus.uploaded):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Урок уже обрабатывается")
    if not lesson.source_path or not Path(lesson.source_path).exists():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Исходный файл урока не найден на диске")
    lesson.status = LessonStatus.uploaded
    lesson.status_message = "Повторная обработка поставлена в очередь"
    lesson.error = None
    lesson.processed_at = None
    await audit(
        session,
        center_id=user.center_id,
        actor_type=ActorType.staff,
        actor_id=user.id,
        action="lesson_reprocess",
        entity="lesson",
        entity_id=lesson.id,
    )
    await session.commit()
    await _enqueue(session, lesson.id)
    await session.commit()
    log.info("lesson_reprocess", lesson_id=str(lesson.id))
    return await fetch_lesson_dto(session, user, lesson.id)


@router.get("/{lesson_id}/transcript", response_model=TranscriptOut)
async def get_transcript(lesson_id: uuid.UUID, user: CurrentStaff, session: DbSession) -> TranscriptOut:
    """Timed transcript with speaker labels."""
    lesson = await get_lesson_or_404(session, user, lesson_id)
    transcript = (
        await session.execute(select(Transcript).where(Transcript.lesson_id == lesson.id))
    ).scalar_one_or_none()
    if transcript is None:
        raise not_found("Транскрипт")
    return TranscriptOut(
        provider=transcript.provider,
        language=transcript.language,
        segments=transcript.segments or [],
        full_text=transcript.full_text,
        speaker_map=transcript.speaker_map or {},
        word_count=transcript.word_count,
        duration_sec=transcript.duration_sec,
    )


@router.get("/{lesson_id}/frames", response_model=list[FrameOut])
async def get_frames(lesson_id: uuid.UUID, user: CurrentStaff, session: DbSession) -> list[FrameOut]:
    """Keyframes of the lesson ordered by index; ``url`` points to the media endpoint."""
    lesson = await get_lesson_or_404(session, user, lesson_id)
    frames = (await session.execute(select(Frame).where(Frame.lesson_id == lesson.id).order_by(Frame.index))).scalars()
    return [
        FrameOut(
            id=f.id,
            index=f.index,
            timestamp_sec=f.timestamp_sec,
            url=frame_url(f.id),
            reason=f.reason,
            width=f.width,
            height=f.height,
        )
        for f in frames
    ]


@router.get("/{lesson_id}/analysis", response_model=AnalysisOut)
async def get_analysis(lesson_id: uuid.UUID, user: CurrentStaff, session: DbSession) -> AnalysisOut:
    """The validated ``LessonAnalysis`` JSON plus provider metadata."""
    lesson = await get_lesson_or_404(session, user, lesson_id)
    analysis = (await session.execute(select(Analysis).where(Analysis.lesson_id == lesson.id))).scalar_one_or_none()
    if analysis is None:
        raise not_found("Анализ")
    return AnalysisOut(
        provider=analysis.provider,
        model=analysis.model,
        prompt_version=analysis.prompt_version,
        data=analysis.data,
        tokens_in=analysis.tokens_in,
        tokens_out=analysis.tokens_out,
        latency_ms=analysis.latency_ms,
        created_at=analysis.created_at,
    )
