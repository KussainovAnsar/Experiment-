"""/api/media — keyframes and source recordings, served only through tenant-checked API."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.deps import CurrentMediaPrincipal, DbSession, MediaPrincipal, is_teacher, not_found
from app.db.models import Frame, Lesson, ParentStudent
from app.services.storage import is_within_media_root

router = APIRouter(prefix="/api/media", tags=["media"])

_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".aac": "audio/aac",
}


async def _lesson_visible(session, principal: MediaPrincipal, lesson: Lesson) -> bool:  # noqa: ANN001
    """Staff of the same centre (teachers: own lessons) or a parent linked to the lesson's student."""
    if principal.staff is not None:
        user = principal.staff
        if lesson.center_id != user.center_id:
            return False
        if is_teacher(user):
            return user.teacher_id is not None and lesson.teacher_id == user.teacher_id
        return True
    if principal.parent is not None:
        parent = principal.parent
        if lesson.center_id != parent.center_id:
            return False
        link = await session.execute(
            select(ParentStudent.id).where(
                ParentStudent.parent_id == parent.id, ParentStudent.student_id == lesson.student_id
            )
        )
        return link.first() is not None
    return False


def _file_or_404(raw_path: str | None, entity: str) -> Path:
    if not raw_path:
        raise not_found(entity)
    path = Path(raw_path)
    if not is_within_media_root(path) or not path.is_file():
        raise not_found(entity)
    return path


@router.get("/frames/{frame_id}")
async def get_frame(frame_id: uuid.UUID, principal: CurrentMediaPrincipal, session: DbSession) -> FileResponse:
    """JPEG keyframe. Accepts the JWT in the header or ``?access_token=`` (for <img> tags)."""
    frame = await session.get(Frame, frame_id)
    if frame is None:
        raise not_found("Кадр")
    lesson = await session.get(Lesson, frame.lesson_id)
    if lesson is None or not await _lesson_visible(session, principal, lesson):
        raise not_found("Кадр")
    path = _file_or_404(frame.path, "Кадр")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/lessons/{lesson_id}/source")
async def get_source(lesson_id: uuid.UUID, principal: CurrentMediaPrincipal, session: DbSession) -> FileResponse:
    """Original recording (staff only). Supports range requests via FileResponse."""
    if principal.staff is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступ только для сотрудников центра")
    lesson = await session.get(Lesson, lesson_id)
    if lesson is None or not await _lesson_visible(session, principal, lesson):
        raise not_found("Урок")
    path = _file_or_404(lesson.source_path, "Файл урока")
    media_type = _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=lesson.source_filename or path.name)
