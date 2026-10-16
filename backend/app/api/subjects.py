"""GET /api/subjects — lesson subject codes and titles (single source: :mod:`app.subjects`)."""

from __future__ import annotations

from fastapi import APIRouter

from app.schemas.api import SubjectOut
from app.subjects import SUBJECTS

router = APIRouter(prefix="/api", tags=["subjects"])


@router.get("/subjects", response_model=list[SubjectOut])
async def list_subjects() -> list[SubjectOut]:
    """Public reference list (no personal data); order = order in UI selectors."""
    return [SubjectOut(code=code, title=title) for code, title in SUBJECTS.items()]
