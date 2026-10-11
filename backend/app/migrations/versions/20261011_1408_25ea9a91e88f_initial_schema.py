"""initial schema

Revision ID: 25ea9a91e88f
Revises:
Create Date: 2026-10-11 14:08:16.474608
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "25ea9a91e88f"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Postgres ENUM types are shared across tables (e.g. "language", "channel"), so they are
# created exactly once here and referenced with create_type=False in the table definitions.
ENUMS: dict[str, tuple[str, ...]] = {
    "language": ("ru", "kk"),
    "job_status": ("queued", "running", "done", "failed"),
    "actor_type": ("staff", "parent", "system"),
    "channel": ("telegram", "whatsapp", "none"),
    "media_kind": ("video", "audio"),
    "lesson_status": (
        "uploaded",
        "probing",
        "extracting_audio",
        "transcribing",
        "extracting_frames",
        "analyzing",
        "reporting",
        "notifying",
        "done",
        "failed",
    ),
    "staff_role": ("owner", "methodist", "teacher"),
    "frame_reason": ("scene", "interval"),
    "notification_status": ("pending", "sent", "failed", "skipped"),
    "report_kind": ("teacher", "manager", "parent"),
    "skill_status": ("struggling", "developing", "mastered"),
}


def _enum(name: str) -> postgresql.ENUM:
    """Reference an already-created Postgres ENUM type without emitting CREATE TYPE."""
    return postgresql.ENUM(*ENUMS[name], name=name, create_type=False)


def _create_enums() -> None:
    bind = op.get_bind()
    for name, values in ENUMS.items():
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)


def _drop_enums() -> None:
    bind = op.get_bind()
    for name, values in reversed(list(ENUMS.items())):
        postgresql.ENUM(*values, name=name).drop(bind, checkfirst=True)


def upgrade() -> None:
    _create_enums()
    op.create_table(
        "centers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("default_language", _enum("language"), nullable=False),
        sa.Column(
            "settings", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_centers")),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", _enum("job_status"), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=128), nullable=True),
        sa.Column("run_after", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    op.create_index("ix_jobs_status_run_after", "jobs", ["status", "run_after"], unique=False)
    op.create_table(
        "audit_log",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("center_id", sa.UUID(), nullable=True),
        sa.Column("actor_type", _enum("actor_type"), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("entity", sa.String(length=64), nullable=True),
        sa.Column("entity_id", sa.UUID(), nullable=True),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["center_id"], ["centers.id"], name=op.f("fk_audit_log_center_id_centers"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(op.f("ix_audit_log_center_id"), "audit_log", ["center_id"], unique=False)
    op.create_table(
        "parents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("center_id", sa.UUID(), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("preferred_channel", _enum("channel"), nullable=False),
        sa.Column("preferred_language", _enum("language"), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("invite_code", sa.String(length=16), nullable=False),
        sa.Column("whatsapp_phone", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["center_id"], ["centers.id"], name=op.f("fk_parents_center_id_centers"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_parents")),
        sa.UniqueConstraint("invite_code", name=op.f("uq_parents_invite_code")),
        sa.UniqueConstraint("telegram_chat_id", name=op.f("uq_parents_telegram_chat_id")),
    )
    op.create_index(op.f("ix_parents_center_id"), "parents", ["center_id"], unique=False)
    op.create_table(
        "students",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("center_id", sa.UUID(), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("grade", sa.Integer(), nullable=True),
        sa.Column("school", sa.String(length=200), nullable=True),
        sa.Column("native_language", _enum("language"), nullable=False),
        sa.Column("target_subject", sa.String(length=64), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["center_id"], ["centers.id"], name=op.f("fk_students_center_id_centers"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_students")),
    )
    op.create_index(op.f("ix_students_center_id"), "students", ["center_id"], unique=False)
    op.create_table(
        "teachers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("center_id", sa.UUID(), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("subjects", sa.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["center_id"], ["centers.id"], name=op.f("fk_teachers_center_id_centers"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_teachers")),
    )
    op.create_index(op.f("ix_teachers_center_id"), "teachers", ["center_id"], unique=False)
    op.create_table(
        "lessons",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("center_id", sa.UUID(), nullable=False),
        sa.Column("teacher_id", sa.UUID(), nullable=False),
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("subject", sa.String(length=64), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_sec", sa.Float(), nullable=True),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column("source_path", sa.String(length=1024), nullable=True),
        sa.Column("audio_path", sa.String(length=1024), nullable=True),
        sa.Column("media_kind", _enum("media_kind"), nullable=False),
        sa.Column("status", _enum("lesson_status"), nullable=False),
        sa.Column("status_message", sa.String(length=500), nullable=True),
        sa.Column("consent_confirmed", sa.Boolean(), nullable=False),
        sa.Column("language_hints", sa.ARRAY(sa.String(length=8)), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["center_id"], ["centers.id"], name=op.f("fk_lessons_center_id_centers"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["student_id"], ["students.id"], name=op.f("fk_lessons_student_id_students"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"], ["teachers.id"], name=op.f("fk_lessons_teacher_id_teachers"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lessons")),
    )
    op.create_index(op.f("ix_lessons_center_id"), "lessons", ["center_id"], unique=False)
    op.create_index("ix_lessons_center_scheduled", "lessons", ["center_id", "scheduled_at"], unique=False)
    op.create_index(op.f("ix_lessons_status"), "lessons", ["status"], unique=False)
    op.create_index(op.f("ix_lessons_student_id"), "lessons", ["student_id"], unique=False)
    op.create_index(op.f("ix_lessons_teacher_id"), "lessons", ["teacher_id"], unique=False)
    op.create_table(
        "magic_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["parents.id"], name=op.f("fk_magic_links_parent_id_parents"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_magic_links")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_magic_links_token_hash")),
    )
    op.create_index(op.f("ix_magic_links_parent_id"), "magic_links", ["parent_id"], unique=False)
    op.create_table(
        "parent_students",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=False),
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("relation", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["parents.id"], name=op.f("fk_parent_students_parent_id_parents"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["student_id"], ["students.id"], name=op.f("fk_parent_students_student_id_students"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_parent_students")),
        sa.UniqueConstraint("parent_id", "student_id", name="uq_parent_students_pair"),
    )
    op.create_index(op.f("ix_parent_students_parent_id"), "parent_students", ["parent_id"], unique=False)
    op.create_index(op.f("ix_parent_students_student_id"), "parent_students", ["student_id"], unique=False)
    op.create_table(
        "staff_users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("center_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("role", _enum("staff_role"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("teacher_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["center_id"], ["centers.id"], name=op.f("fk_staff_users_center_id_centers"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"], ["teachers.id"], name=op.f("fk_staff_users_teacher_id_teachers"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staff_users")),
        sa.UniqueConstraint("email", name=op.f("uq_staff_users_email")),
    )
    op.create_index(op.f("ix_staff_users_center_id"), "staff_users", ["center_id"], unique=False)
    op.create_table(
        "analyses",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("lesson_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["lesson_id"], ["lessons.id"], name=op.f("fk_analyses_lesson_id_lessons"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analyses")),
        sa.UniqueConstraint("lesson_id", name=op.f("uq_analyses_lesson_id")),
    )
    op.create_table(
        "frames",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("lesson_id", sa.UUID(), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("timestamp_sec", sa.Float(), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("phash", sa.String(length=32), nullable=True),
        sa.Column("reason", _enum("frame_reason"), nullable=False),
        sa.Column("selected_for_llm", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["lesson_id"], ["lessons.id"], name=op.f("fk_frames_lesson_id_lessons"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_frames")),
        sa.UniqueConstraint("lesson_id", "index", name="uq_frames_lesson_index"),
    )
    op.create_index(op.f("ix_frames_lesson_id"), "frames", ["lesson_id"], unique=False)
    op.create_table(
        "notifications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("center_id", sa.UUID(), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("lesson_id", sa.UUID(), nullable=True),
        sa.Column("channel", _enum("channel"), nullable=False),
        sa.Column("status", _enum("notification_status"), nullable=False),
        sa.Column("provider_message_id", sa.String(length=128), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["center_id"], ["centers.id"], name=op.f("fk_notifications_center_id_centers"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["lesson_id"], ["lessons.id"], name=op.f("fk_notifications_lesson_id_lessons"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["parents.id"], name=op.f("fk_notifications_parent_id_parents"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
    )
    op.create_index(op.f("ix_notifications_center_id"), "notifications", ["center_id"], unique=False)
    op.create_index(op.f("ix_notifications_lesson_id"), "notifications", ["lesson_id"], unique=False)
    op.create_index(op.f("ix_notifications_parent_id"), "notifications", ["parent_id"], unique=False)
    op.create_table(
        "reports",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("lesson_id", sa.UUID(), nullable=False),
        sa.Column("kind", _enum("report_kind"), nullable=False),
        sa.Column("language", _enum("language"), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("telegram_html", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["lesson_id"], ["lessons.id"], name=op.f("fk_reports_lesson_id_lessons"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reports")),
        sa.UniqueConstraint("lesson_id", "kind", "version", name="uq_reports_lesson_kind_version"),
    )
    op.create_index(op.f("ix_reports_lesson_id"), "reports", ["lesson_id"], unique=False)
    op.create_table(
        "skill_states",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("skill_key", sa.String(length=80), nullable=False),
        sa.Column("skill_name", sa.String(length=200), nullable=False),
        sa.Column("status", _enum("skill_status"), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("first_seen_lesson_id", sa.UUID(), nullable=True),
        sa.Column("last_seen_lesson_id", sa.UUID(), nullable=True),
        sa.Column("times_seen", sa.Integer(), nullable=False),
        sa.Column("times_struggled", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["first_seen_lesson_id"],
            ["lessons.id"],
            name=op.f("fk_skill_states_first_seen_lesson_id_lessons"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["last_seen_lesson_id"],
            ["lessons.id"],
            name=op.f("fk_skill_states_last_seen_lesson_id_lessons"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["student_id"], ["students.id"], name=op.f("fk_skill_states_student_id_students"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skill_states")),
        sa.UniqueConstraint("student_id", "skill_key", name="uq_skill_states_student_skill"),
    )
    op.create_index(op.f("ix_skill_states_student_id"), "skill_states", ["student_id"], unique=False)
    op.create_table(
        "transcripts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("lesson_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("segments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("full_text", sa.Text(), nullable=False),
        sa.Column("speaker_map", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.Column("duration_sec", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["lesson_id"], ["lessons.id"], name=op.f("fk_transcripts_lesson_id_lessons"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transcripts")),
        sa.UniqueConstraint("lesson_id", name=op.f("uq_transcripts_lesson_id")),
    )


def downgrade() -> None:
    op.drop_table("transcripts")
    op.drop_index(op.f("ix_skill_states_student_id"), table_name="skill_states")
    op.drop_table("skill_states")
    op.drop_index(op.f("ix_reports_lesson_id"), table_name="reports")
    op.drop_table("reports")
    op.drop_index(op.f("ix_notifications_parent_id"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_lesson_id"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_center_id"), table_name="notifications")
    op.drop_table("notifications")
    op.drop_index(op.f("ix_frames_lesson_id"), table_name="frames")
    op.drop_table("frames")
    op.drop_table("analyses")
    op.drop_index(op.f("ix_staff_users_center_id"), table_name="staff_users")
    op.drop_table("staff_users")
    op.drop_index(op.f("ix_parent_students_student_id"), table_name="parent_students")
    op.drop_index(op.f("ix_parent_students_parent_id"), table_name="parent_students")
    op.drop_table("parent_students")
    op.drop_index(op.f("ix_magic_links_parent_id"), table_name="magic_links")
    op.drop_table("magic_links")
    op.drop_index(op.f("ix_lessons_teacher_id"), table_name="lessons")
    op.drop_index(op.f("ix_lessons_student_id"), table_name="lessons")
    op.drop_index(op.f("ix_lessons_status"), table_name="lessons")
    op.drop_index("ix_lessons_center_scheduled", table_name="lessons")
    op.drop_index(op.f("ix_lessons_center_id"), table_name="lessons")
    op.drop_table("lessons")
    op.drop_index(op.f("ix_teachers_center_id"), table_name="teachers")
    op.drop_table("teachers")
    op.drop_index(op.f("ix_students_center_id"), table_name="students")
    op.drop_table("students")
    op.drop_index(op.f("ix_parents_center_id"), table_name="parents")
    op.drop_table("parents")
    op.drop_index(op.f("ix_audit_log_center_id"), table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_jobs_status_run_after", table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("centers")
    _drop_enums()
