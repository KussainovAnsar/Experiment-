"""All ORM models. See docs/CONTRACT.md §4 — this file is the single source of truth for the schema."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------------------------------------------------------- enums


class StaffRole(str, enum.Enum):
    owner = "owner"
    methodist = "methodist"
    teacher = "teacher"


class Channel(str, enum.Enum):
    telegram = "telegram"
    whatsapp = "whatsapp"
    none = "none"


class Language(str, enum.Enum):
    ru = "ru"
    kk = "kk"


class MediaKind(str, enum.Enum):
    video = "video"
    audio = "audio"


class LessonStatus(str, enum.Enum):
    uploaded = "uploaded"
    probing = "probing"
    extracting_audio = "extracting_audio"
    transcribing = "transcribing"
    extracting_frames = "extracting_frames"
    analyzing = "analyzing"
    reporting = "reporting"
    notifying = "notifying"
    done = "done"
    failed = "failed"


# Ordered stages, used by the pipeline runner and the frontend progress bar.
LESSON_STAGE_ORDER: list[LessonStatus] = [
    LessonStatus.uploaded,
    LessonStatus.probing,
    LessonStatus.extracting_audio,
    LessonStatus.transcribing,
    LessonStatus.extracting_frames,
    LessonStatus.analyzing,
    LessonStatus.reporting,
    LessonStatus.notifying,
    LessonStatus.done,
]


class ReportKind(str, enum.Enum):
    teacher = "teacher"
    manager = "manager"
    parent = "parent"


class SkillStatus(str, enum.Enum):
    struggling = "struggling"
    developing = "developing"
    mastered = "mastered"


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


class NotificationStatus(str, enum.Enum):
    pending = "pending"
    sent = "sent"
    failed = "failed"
    skipped = "skipped"


class FrameReason(str, enum.Enum):
    scene = "scene"
    interval = "interval"


class ActorType(str, enum.Enum):
    staff = "staff"
    parent = "parent"
    system = "system"


# ---------------------------------------------------------------- tenant


class Center(TimestampMixin, Base):
    __tablename__ = "centers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    city: Mapped[str | None] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Almaty", nullable=False)
    default_language: Mapped[Language] = mapped_column(
        Enum(Language, name="language"), default=Language.ru, nullable=False
    )
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False)

    staff: Mapped[list[StaffUser]] = relationship(back_populates="center", cascade="all, delete-orphan")


class StaffUser(TimestampMixin, Base):
    __tablename__ = "staff_users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    center_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("centers.id", ondelete="CASCADE"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[StaffRole] = mapped_column(Enum(StaffRole, name="staff_role"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    teacher_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))

    center: Mapped[Center] = relationship(back_populates="staff")
    teacher: Mapped[Teacher | None] = relationship(foreign_keys=[teacher_id])


# ---------------------------------------------------------------- people


class Teacher(TimestampMixin, Base):
    __tablename__ = "teachers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    center_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("centers.id", ondelete="CASCADE"), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    subjects: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    lessons: Mapped[list[Lesson]] = relationship(back_populates="teacher")


class Student(TimestampMixin, Base):
    __tablename__ = "students"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    center_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("centers.id", ondelete="CASCADE"), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    grade: Mapped[int | None] = mapped_column(Integer)
    school: Mapped[str | None] = mapped_column(String(200))
    native_language: Mapped[Language] = mapped_column(Enum(Language, name="language"), default=Language.kk, nullable=False)
    target_subject: Mapped[str] = mapped_column(String(64), default="russian", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    lessons: Mapped[list[Lesson]] = relationship(back_populates="student")
    parent_links: Mapped[list[ParentStudent]] = relationship(back_populates="student", cascade="all, delete-orphan")
    skill_states: Mapped[list[SkillState]] = relationship(back_populates="student", cascade="all, delete-orphan")


class Parent(TimestampMixin, Base):
    __tablename__ = "parents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    center_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("centers.id", ondelete="CASCADE"), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(255))
    preferred_channel: Mapped[Channel] = mapped_column(Enum(Channel, name="channel"), default=Channel.telegram, nullable=False)
    preferred_language: Mapped[Language] = mapped_column(Enum(Language, name="language"), default=Language.ru, nullable=False)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    invite_code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    whatsapp_phone: Mapped[str | None] = mapped_column(String(32))

    student_links: Mapped[list[ParentStudent]] = relationship(back_populates="parent", cascade="all, delete-orphan")


class ParentStudent(Base):
    __tablename__ = "parent_students"
    __table_args__ = (UniqueConstraint("parent_id", "student_id", name="uq_parent_students_pair"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    parent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("parents.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    relation: Mapped[str | None] = mapped_column(String(64))

    parent: Mapped[Parent] = relationship(back_populates="student_links")
    student: Mapped[Student] = relationship(back_populates="parent_links")


# ---------------------------------------------------------------- lessons & pipeline artefacts


class Lesson(TimestampMixin, Base):
    __tablename__ = "lessons"
    __table_args__ = (Index("ix_lessons_center_scheduled", "center_id", "scheduled_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    center_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("centers.id", ondelete="CASCADE"), nullable=False, index=True)
    teacher_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teachers.id", ondelete="RESTRICT"), nullable=False, index=True)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(64), default="russian", nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_sec: Mapped[float | None] = mapped_column(Float)
    source_filename: Mapped[str | None] = mapped_column(String(255))
    source_path: Mapped[str | None] = mapped_column(String(1024))
    audio_path: Mapped[str | None] = mapped_column(String(1024))
    media_kind: Mapped[MediaKind] = mapped_column(Enum(MediaKind, name="media_kind"), default=MediaKind.video, nullable=False)
    status: Mapped[LessonStatus] = mapped_column(
        Enum(LessonStatus, name="lesson_status"), default=LessonStatus.uploaded, nullable=False, index=True
    )
    status_message: Mapped[str | None] = mapped_column(String(500))
    consent_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    language_hints: Mapped[list[str]] = mapped_column(ARRAY(String(8)), default=lambda: ["ru", "kk"], nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # {"total_sec", "stages_ms": {stage: ms}, "media_duration_sec", "speed_x", "stt_provider", ...}
    processing_stats: Mapped[dict | None] = mapped_column(JSONB)

    teacher: Mapped[Teacher] = relationship(back_populates="lessons")
    student: Mapped[Student] = relationship(back_populates="lessons")
    transcript: Mapped[Transcript | None] = relationship(back_populates="lesson", uselist=False, cascade="all, delete-orphan")
    frames: Mapped[list[Frame]] = relationship(back_populates="lesson", cascade="all, delete-orphan", order_by="Frame.index")
    analysis: Mapped[Analysis | None] = relationship(back_populates="lesson", uselist=False, cascade="all, delete-orphan")
    reports: Mapped[list[Report]] = relationship(back_populates="lesson", cascade="all, delete-orphan")


class Transcript(TimestampMixin, Base):
    __tablename__ = "transcripts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    lesson_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str | None] = mapped_column(String(16))
    # list of {start: float, end: float, speaker: "teacher"|"student"|"unknown", text: str, confidence: float|None}
    segments: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    full_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    speaker_map: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_sec: Mapped[float | None] = mapped_column(Float)

    lesson: Mapped[Lesson] = relationship(back_populates="transcript")


class Frame(Base):
    __tablename__ = "frames"
    __table_args__ = (UniqueConstraint("lesson_id", "index", name="uq_frames_lesson_index"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    lesson_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True)
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp_sec: Mapped[float] = mapped_column(Float, nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    phash: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[FrameReason] = mapped_column(Enum(FrameReason, name="frame_reason"), default=FrameReason.interval, nullable=False)
    selected_for_llm: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    lesson: Mapped[Lesson] = relationship(back_populates="frames")


class Analysis(TimestampMixin, Base):
    __tablename__ = "analyses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    lesson_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(32), default="1.0", nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)  # validated LessonAnalysis
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)

    lesson: Mapped[Lesson] = relationship(back_populates="analysis")


class Report(TimestampMixin, Base):
    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("lesson_id", "kind", "version", name="uq_reports_lesson_kind_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    lesson_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[ReportKind] = mapped_column(Enum(ReportKind, name="report_kind"), nullable=False)
    language: Mapped[Language] = mapped_column(Enum(Language, name="language"), default=Language.ru, nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    markdown: Mapped[str] = mapped_column(Text, default="", nullable=False)
    telegram_html: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    lesson: Mapped[Lesson] = relationship(back_populates="reports")


class SkillState(TimestampMixin, Base):
    __tablename__ = "skill_states"
    __table_args__ = (UniqueConstraint("student_id", "skill_key", name="uq_skill_states_student_skill"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    skill_key: Mapped[str] = mapped_column(String(80), nullable=False)
    skill_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[SkillStatus] = mapped_column(Enum(SkillStatus, name="skill_status"), nullable=False)
    # list of {lesson_id, timestamp_sec, note, status}
    evidence: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    first_seen_lesson_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("lessons.id", ondelete="SET NULL"))
    last_seen_lesson_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("lessons.id", ondelete="SET NULL"))
    times_seen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    times_struggled: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    student: Mapped[Student] = relationship(back_populates="skill_states")


# ---------------------------------------------------------------- infrastructure


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_run_after", "status", "run_after"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus, name="job_status"), default=JobStatus.queued, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(128))
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)


class Notification(TimestampMixin, Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    center_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("centers.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("parents.id", ondelete="SET NULL"), index=True)
    lesson_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("lessons.id", ondelete="SET NULL"), index=True)
    channel: Mapped[Channel] = mapped_column(Enum(Channel, name="channel"), nullable=False)
    status: Mapped[NotificationStatus] = mapped_column(
        Enum(NotificationStatus, name="notification_status"), default=NotificationStatus.pending, nullable=False
    )
    provider_message_id: Mapped[str | None] = mapped_column(String(128))
    error: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MagicLink(Base):
    __tablename__ = "magic_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    parent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("parents.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), default="portal", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    center_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("centers.id", ondelete="CASCADE"), index=True)
    actor_type: Mapped[ActorType] = mapped_column(Enum(ActorType, name="actor_type"), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
