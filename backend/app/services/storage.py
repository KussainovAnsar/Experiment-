"""Filesystem layout under MEDIA_ROOT: media/{center_id}/{lesson_id}/{source.ext|audio.wav|audio.ogg|frames/*.jpg}."""

from __future__ import annotations

import uuid
from pathlib import Path

from app.config import get_settings


def lesson_dir(center_id: uuid.UUID, lesson_id: uuid.UUID) -> Path:
    """Return (and create) ``MEDIA_ROOT/{center_id}/{lesson_id}``."""
    path = get_settings().media_root / str(center_id) / str(lesson_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def frames_dir(center_id: uuid.UUID, lesson_id: uuid.UUID) -> Path:
    """Return (and create) the keyframes directory of a lesson."""
    path = lesson_dir(center_id, lesson_id) / "frames"
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_extension(original_filename: str) -> str:
    """Return the lower-cased extension (with dot) of an uploaded filename, or ''."""
    name = Path(original_filename or "").name
    suffix = Path(name).suffix.lower()
    if not suffix or len(suffix) > 10 or not suffix[1:].isalnum():
        return ""
    return suffix


def source_path(center_id: uuid.UUID, lesson_id: uuid.UUID, original_filename: str) -> Path:
    """Return the path of the uploaded source media: ``.../source<ext>``."""
    return lesson_dir(center_id, lesson_id) / f"source{safe_extension(original_filename)}"


def audio_path(center_id: uuid.UUID, lesson_id: uuid.UUID) -> Path:
    """Return the path of the extracted 16 kHz mono wav: ``.../audio.wav``."""
    return lesson_dir(center_id, lesson_id) / "audio.wav"


def compact_audio_path(center_id: uuid.UUID, lesson_id: uuid.UUID) -> Path:
    """Return the path of the compact Ogg/Opus copy uploaded to cloud STT providers: ``.../audio.ogg``."""
    return lesson_dir(center_id, lesson_id) / "audio.ogg"


def is_within_media_root(path: Path) -> bool:
    """True when ``path`` resolves inside MEDIA_ROOT (defence against stored-path tampering)."""
    root = get_settings().media_root.resolve()
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False
    return True
