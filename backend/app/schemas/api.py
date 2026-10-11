"""Pydantic DTOs for the HTTP API (docs/CONTRACT.md §6). ORM models are never returned directly."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.db.models import (
    Channel,
    FrameReason,
    Language,
    LessonStatus,
    MediaKind,
    NotificationStatus,
    ReportKind,
    StaffRole,
)

T = TypeVar("T")


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    """Paginated list envelope."""

    items: list[T]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------- auth / staff


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class RegisterCenterRequest(BaseModel):
    center_name: str = Field(min_length=2, max_length=200)
    city: str | None = Field(default=None, max_length=120)
    owner_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)


class StaffUserOut(_Out):
    id: uuid.UUID
    email: str
    full_name: str
    role: StaffRole
    center_id: uuid.UUID
    center_name: str
    teacher_id: uuid.UUID | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: StaffUserOut


# ---------------------------------------------------------------- center


class CenterOut(_Out):
    id: uuid.UUID
    name: str
    city: str | None
    timezone: str
    default_language: Language
    settings: dict[str, Any]
    created_at: datetime


class CenterUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    city: str | None = Field(default=None, max_length=120)
    timezone: str | None = Field(default=None, max_length=64)
    default_language: Language | None = None
    settings: dict[str, Any] | None = None


# ---------------------------------------------------------------- people


class TeacherCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    subjects: list[str] = Field(default_factory=lambda: ["russian"])
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=32)
    notes: str | None = None


class TeacherUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    subjects: list[str] | None = None
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=32)
    notes: str | None = None
    is_active: bool | None = None


class TeacherOut(_Out):
    id: uuid.UUID
    center_id: uuid.UUID
    full_name: str
    subjects: list[str]
    email: str | None
    phone: str | None
    notes: str | None
    is_active: bool
    created_at: datetime


class StudentCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    grade: int | None = Field(default=None, ge=0, le=12)
    school: str | None = Field(default=None, max_length=200)
    native_language: Language = Language.kk
    target_subject: str = Field(default="russian", max_length=64)
    notes: str | None = None


class StudentUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    grade: int | None = Field(default=None, ge=0, le=12)
    school: str | None = Field(default=None, max_length=200)
    native_language: Language | None = None
    target_subject: str | None = Field(default=None, max_length=64)
    notes: str | None = None
    is_active: bool | None = None


class StudentOut(_Out):
    id: uuid.UUID
    center_id: uuid.UUID
    full_name: str
    grade: int | None
    school: str | None
    native_language: Language
    target_subject: str
    notes: str | None
    is_active: bool
    created_at: datetime


class ParentCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    email: EmailStr | None = None
    preferred_channel: Channel = Channel.telegram
    preferred_language: Language = Language.ru
    whatsapp_phone: str | None = Field(default=None, max_length=32)
    student_ids: list[uuid.UUID] = Field(default_factory=list, description="Students to link on creation")
    relation: str | None = Field(default=None, max_length=64)


class ParentUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    email: EmailStr | None = None
    preferred_channel: Channel | None = None
    preferred_language: Language | None = None
    whatsapp_phone: str | None = Field(default=None, max_length=32)


class LinkedStudentOut(BaseModel):
    id: uuid.UUID
    full_name: str
    grade: int | None
    relation: str | None


class ParentOut(_Out):
    id: uuid.UUID
    center_id: uuid.UUID
    full_name: str
    phone: str | None
    email: str | None
    preferred_channel: Channel
    preferred_language: Language
    telegram_chat_id: int | None
    telegram_linked: bool
    invite_code: str
    whatsapp_phone: str | None
    students: list[LinkedStudentOut] = Field(default_factory=list)
    created_at: datetime


class LinkStudentRequest(BaseModel):
    student_id: uuid.UUID
    relation: str | None = Field(default=None, max_length=64)


class MagicLinkOut(BaseModel):
    url: str


class NotificationOut(_Out):
    """A delivery attempt. ``payload`` never contains invite secrets for the ``teacher`` role (``deep_link``,
    ``magic_url`` are stripped); the invite ``magic_url`` is returned to managers but not persisted."""

    id: uuid.UUID
    parent_id: uuid.UUID | None
    lesson_id: uuid.UUID | None
    channel: Channel
    status: NotificationStatus
    error: str | None
    payload: dict[str, Any]
    sent_at: datetime | None
    created_at: datetime


# ---------------------------------------------------------------- lessons


class LessonOut(BaseModel):
    id: uuid.UUID
    student_id: uuid.UUID
    student_name: str
    teacher_id: uuid.UUID
    teacher_name: str
    subject: str
    scheduled_at: datetime
    duration_sec: float | None
    media_kind: MediaKind
    source_filename: str | None
    status: LessonStatus
    status_message: str | None
    error: str | None
    stage_index: int | None = Field(description="Position in LESSON_STAGE_ORDER (0 = uploaded); null when failed")
    stage_total: int = Field(description="Index of the final stage (done); progress = stage_index / stage_total")
    has_transcript: bool
    has_analysis: bool
    report_kinds: list[ReportKind]
    language_hints: list[str]
    consent_confirmed: bool
    created_at: datetime
    processed_at: datetime | None
    processing_stats: dict[str, Any] | None = Field(
        default=None,
        description="Wall-clock of the last processing run: total_sec, stages_ms, media_duration_sec, speed_x",
    )


class FrameOut(BaseModel):
    id: uuid.UUID
    index: int
    timestamp_sec: float
    url: str
    reason: FrameReason
    width: int | None
    height: int | None


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(extra="allow")

    start: float
    end: float
    speaker: str = "unknown"
    text: str = ""
    confidence: float | None = None


class TranscriptOut(BaseModel):
    provider: str
    language: str | None
    segments: list[TranscriptSegment]
    full_text: str
    speaker_map: dict[str, Any]
    word_count: int
    duration_sec: float | None


class MediaTokenOut(BaseModel):
    """Short-lived token for ``?access_token=`` on ``/api/media/*`` URLs of one lesson."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    lesson_id: uuid.UUID
    expires_at: datetime


class AnalysisOut(BaseModel):
    """``LessonAnalysis`` JSON; for the ``teacher`` role ``data`` has ``manager_indicators`` set to ``null``."""

    provider: str
    model: str | None
    prompt_version: str
    data: dict[str, Any]
    tokens_in: int | None
    tokens_out: int | None
    latency_ms: int | None
    created_at: datetime


class ReportOut(_Out):
    kind: ReportKind
    language: Language
    data: dict[str, Any]
    markdown: str
    telegram_html: str | None
    version: int
    created_at: datetime


# ---------------------------------------------------------------- dashboard


class DashboardOut(BaseModel):
    lessons_total: int
    lessons_last_7_days: int
    lessons_processing: int
    lessons_failed: int
    share_objectives_checked: float | None = Field(description="Share of done lessons with objectives_checked=true")
    needs_review_count: int | None = Field(description="null for the teacher role (manager-only indicator)")
    recent_lessons: list[LessonOut]
    needs_review_lessons: list[LessonOut]


# ---------------------------------------------------------------- parent portal


class ParentExchangeRequest(BaseModel):
    # urlsafe base64 alphabet only: anything else is rejected with 422 before it reaches the hash.
    token: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class ParentMeOut(_Out):
    id: uuid.UUID
    full_name: str
    preferred_channel: Channel
    preferred_language: Language
    telegram_linked: bool
    center_name: str


class ParentMeUpdate(BaseModel):
    preferred_channel: Channel | None = None
    preferred_language: Language | None = None


class StudentBrief(BaseModel):
    id: uuid.UUID
    full_name: str
    grade: int | None


class SkillsSummary(BaseModel):
    struggling: int = 0
    developing: int = 0
    mastered: int = 0


class ParentStudentCard(BaseModel):
    student: StudentBrief
    last_lesson_at: datetime | None
    lessons_count: int
    skills_summary: SkillsSummary


class ParentExchangeResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    parent: ParentMeOut
    students: list[StudentBrief]


class ParentLessonCard(BaseModel):
    lesson_id: uuid.UUID
    scheduled_at: datetime
    topic_plain: str | None
    encouragement: str | None
    what_to_repeat_count: int


class ParentFrameOut(BaseModel):
    """A keyframe the parent report points at (an error, a success or a visual issue), with a plain caption."""

    index: int
    timestamp_sec: float
    url: str
    caption: str | None = None


class ParentLessonReportOut(BaseModel):
    lesson_id: uuid.UUID
    scheduled_at: datetime
    teacher_name: str
    data: dict[str, Any]
    frames: list[ParentFrameOut]
    media_token: str | None = Field(default=None, description="Short-lived ?access_token= for the frame URLs")


# ---------------------------------------------------------------- misc


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "error"]
    version: str
    app_env: str


class MessageOut(BaseModel):
    detail: str
