"""Demo seed: ``python -m app.seed_demo [--reset] [--reprocess] [--realtime]``.

Creates (idempotently) the demo centre «Білім Online» with three staff logins, the teacher Айгерим Сериковна,
the student Данияр Ахметов, his mother Гульнара Ахметова (invite code ``DEMO2026``) and two lessons (10 and 3
days ago) whose source video is ``demo/lesson_01/lesson.mp4`` (built on the fly from the demo frames when
missing). Each lesson is then processed by the real pipeline (:func:`app.pipeline.runner.run_lesson_pipeline`)
with the mock STT / LLM providers forced, so transcripts, frames, analysis, reports, skill states and
notifications all come from the production code path. With the compressed (~90 s) demo video the frame
timecodes and the lesson duration are then aligned to ``demo/lesson_01/frames/frames.json`` (the indices already
match), so report timecodes highlight the right keyframe in the UI; ``--realtime`` makes this unnecessary. At
the end the script prints the logins, a fresh parent magic link and the Telegram deep-link instructions.

Flags:
    --reset       delete the demo centre (and its media directory) before seeding
    --reprocess   re-run the pipeline for lessons that are already ``done``
    --realtime    build the ~34-minute real-time demo video instead of the ~90-second compressed one

Run from ``backend/`` (or with ``PYTHONPATH=backend``) after ``alembic upgrade head``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# The mock providers must be forced BEFORE Settings is instantiated: pydantic-settings reads the environment
# when the object is created, and ``get_settings`` caches it.
os.environ["STT_PROVIDER"] = "mock"
os.environ["LLM_PROVIDER"] = "mock"
os.environ.setdefault("MESSAGING_PROVIDER", "mock")  # nothing leaves the machine during seeding

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.config import REPO_DIR, get_settings  # noqa: E402

get_settings.cache_clear()

from app.db.models import (  # noqa: E402
    Center,
    Channel,
    Frame,
    Language,
    Lesson,
    LessonStatus,
    MagicLink,
    MediaKind,
    Notification,
    Parent,
    ParentStudent,
    SkillState,
    StaffRole,
    StaffUser,
    Student,
    Teacher,
    Transcript,
)
from app.db.session import get_sessionmaker  # noqa: E402

try:  # structlog config is optional for the seed, the pipeline logs are nicer with it
    from app.logging import configure_logging, get_logger

    configure_logging(level=os.environ.get("SEED_LOG_LEVEL", "INFO"), json_output=False)
    log = get_logger("app.seed_demo")
except Exception:  # pragma: no cover - logging module absent or incompatible
    import logging

    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger("app.seed_demo")  # type: ignore[assignment]

# ---------------------------------------------------------------- demo constants

DEMO_DIR = REPO_DIR / "demo"
LESSON_FIXTURE_DIR = DEMO_DIR / "lesson_01"
DEMO_VIDEO = LESSON_FIXTURE_DIR / "lesson.mp4"
DEMO_VIDEO_REALTIME = LESSON_FIXTURE_DIR / "lesson_realtime.mp4"
FRAMES_FIXTURE = LESSON_FIXTURE_DIR / "frames" / "frames.json"
BUILD_SCRIPT = DEMO_DIR / "build_demo_media.py"

CENTER_NAME = "Білім Online"
CENTER_CITY = "Алматы"
CENTER_TZ = "Asia/Almaty"
DEMO_PASSWORD = "demo12345"
TEACHER_NAME = "Айгерим Сериковна"
STUDENT_NAME = "Данияр Ахметов"
STUDENT_GRADE = 6
STUDENT_SCHOOL = "Школа-гимназия № 12, Алматы"
PARENT_NAME = "Гульнара Ахметова"
PARENT_INVITE_CODE = "DEMO2026"
PARENT_RELATION = "мама"
SOURCE_FILENAME = "demo_lesson_01.mp4"
LESSON_SUBJECT = "russian"
LESSON_DAYS_AGO: tuple[int, ...] = (10, 3)
LESSON_HOUR_LOCAL = 16

STAFF: list[tuple[str, str, StaffRole]] = [
    ("admin@demo.kz", "Асель Нурланова", StaffRole.owner),
    ("methodist@demo.kz", "Жанар Касымова", StaffRole.methodist),
    ("teacher@demo.kz", TEACHER_NAME, StaffRole.teacher),
]

_INVITE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


class SeedError(RuntimeError):
    """A precondition of the seed is not met (database not migrated, ffmpeg missing, …)."""


# ---------------------------------------------------------------- module A helpers with local fallbacks


def hash_password(raw: str) -> str:
    """``app.services.auth.hash_password`` or a bcrypt fallback with identical semantics."""
    try:
        from app.services.auth import hash_password as _hash

        return _hash(raw)
    except ImportError:
        import bcrypt

        return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def generate_invite_code() -> str:
    """``app.services.auth.generate_invite_code`` or an 8-char unambiguous fallback."""
    try:
        from app.services.auth import generate_invite_code as _gen

        return _gen()
    except ImportError:
        return "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(8))


async def create_magic_link(session: AsyncSession, parent: Parent) -> str:
    """``app.services.auth.create_magic_link`` or a local equivalent (sha256 of the token stored)."""
    try:
        from app.services.auth import create_magic_link as _create

        return await _create(session, parent, purpose="portal")
    except ImportError:
        settings = get_settings()
        token = secrets.token_urlsafe(32)
        session.add(
            MagicLink(
                parent_id=parent.id,
                token_hash=hashlib.sha256(token.encode("ascii")).hexdigest(),
                purpose="portal",
                expires_at=datetime.now(UTC) + timedelta(hours=settings.magic_link_ttl_hours),
            )
        )
        await session.flush()
        return f"{settings.public_base_url.rstrip('/')}/p/{token}"


def source_path(center_id: uuid.UUID, lesson_id: uuid.UUID, original_filename: str) -> Path:
    """``app.services.storage.source_path`` or the same ``MEDIA_ROOT/{center}/{lesson}/source<ext>`` layout."""
    try:
        from app.services.storage import source_path as _source_path

        return _source_path(center_id, lesson_id, original_filename)
    except ImportError:
        base = get_settings().media_root / str(center_id) / str(lesson_id)
        base.mkdir(parents=True, exist_ok=True)
        return base / f"source{Path(original_filename).suffix.lower()}"


def deep_link_for(parent: Parent) -> str | None:
    """``app.bot.telegram.deep_link_for`` (None when the bot is disabled or module C is absent)."""
    try:
        from app.bot.telegram import deep_link_for as _deep_link

        return _deep_link(parent)
    except Exception:  # ImportError or a bot module that cannot initialise without a token
        return None


# ---------------------------------------------------------------- demo media


def _load_build_module():
    spec = importlib.util.spec_from_file_location("lessonlens_demo_build", BUILD_SCRIPT)
    if spec is None or spec.loader is None:
        raise SeedError(f"не найден скрипт сборки демо-видео: {BUILD_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def ensure_demo_video(*, realtime: bool) -> Path:
    """Return the demo mp4, building it with ``demo/build_demo_media.py`` when missing."""
    target = DEMO_VIDEO_REALTIME if realtime else DEMO_VIDEO
    if target.is_file() and target.stat().st_size > 0:
        return target
    if shutil.which(get_settings().ffmpeg_bin) is None:
        raise SeedError("ffmpeg не найден: установите ffmpeg или соберите demo/lesson_01/lesson.mp4 вручную")
    log.info("demo_video_build", path=str(target), realtime=realtime)
    module = _load_build_module()
    try:
        module.build(target, realtime=realtime, quiet=False)
    except Exception as exc:  # BuildError or ffmpeg issues
        raise SeedError(f"не удалось собрать демо-видео: {exc}") from exc
    return target


# ---------------------------------------------------------------- database helpers


async def check_schema(session: AsyncSession) -> None:
    """Fail early with a helpful message when migrations have not been applied."""
    try:
        await session.execute(text("SELECT 1 FROM centers LIMIT 1"))
    except (ProgrammingError, OperationalError, DBAPIError) as exc:
        raise SeedError(
            "таблицы не найдены — выполните `alembic upgrade head` в каталоге backend/ "
            f"(БД: {get_settings().database_url.split('@')[-1]}). Детали: {type(exc).__name__}"
        ) from exc


async def find_demo_centers(session: AsyncSession) -> list[Center]:
    return list((await session.execute(select(Center).where(Center.name == CENTER_NAME))).scalars().all())


async def reset_demo(session: AsyncSession) -> int:
    """Delete every demo centre with its dependants and media directory; returns the number removed."""
    centers = await find_demo_centers(session)
    for center in centers:
        log.info("demo_reset", center_id=str(center.id))
        # Lessons first: teachers/students are RESTRICT targets of lessons, so the centre cascade alone could fail.
        lessons = (await session.execute(select(Lesson).where(Lesson.center_id == center.id))).scalars().all()
        for lesson in lessons:
            await session.delete(lesson)
        await session.flush()
        students = (await session.execute(select(Student).where(Student.center_id == center.id))).scalars().all()
        for student in students:
            for state in (
                await session.execute(select(SkillState).where(SkillState.student_id == student.id))
            ).scalars():
                await session.delete(state)
        parents = (await session.execute(select(Parent).where(Parent.center_id == center.id))).scalars().all()
        for parent in parents:
            for link in (await session.execute(select(MagicLink).where(MagicLink.parent_id == parent.id))).scalars():
                await session.delete(link)
        for note in (await session.execute(select(Notification).where(Notification.center_id == center.id))).scalars():
            await session.delete(note)
        await session.flush()
        await session.delete(center)  # cascades staff, teachers, students, parents, parent_students, audit_log
        await session.flush()
        media_dir = get_settings().media_root / str(center.id)
        if media_dir.is_dir():
            shutil.rmtree(media_dir, ignore_errors=True)
    await session.commit()
    return len(centers)


async def get_or_create_center(session: AsyncSession) -> Center:
    centers = await find_demo_centers(session)
    if centers:
        center = centers[0]
        center.city = center.city or CENTER_CITY
        return center
    center = Center(
        name=CENTER_NAME,
        city=CENTER_CITY,
        timezone=CENTER_TZ,
        default_language=Language.ru,
        settings={"demo": True, "notify_parents": True},
    )
    session.add(center)
    await session.flush()
    log.info("demo_center_created", center_id=str(center.id))
    return center


async def get_or_create_teacher(session: AsyncSession, center: Center) -> Teacher:
    teacher = (
        await session.execute(select(Teacher).where(Teacher.center_id == center.id, Teacher.full_name == TEACHER_NAME))
    ).scalar_one_or_none()
    if teacher is None:
        teacher = Teacher(
            center_id=center.id,
            full_name=TEACHER_NAME,
            subjects=["russian"],
            email="teacher@demo.kz",
            notes="Демо-преподаватель. Русский язык как второй, 5–7 классы.",
        )
        session.add(teacher)
        await session.flush()
    return teacher


async def get_or_create_staff(session: AsyncSession, center: Center, teacher: Teacher) -> list[StaffUser]:
    users: list[StaffUser] = []
    for email, full_name, role in STAFF:
        user = (await session.execute(select(StaffUser).where(StaffUser.email == email))).scalar_one_or_none()
        if user is None:
            user = StaffUser(
                center_id=center.id,
                email=email,
                password_hash=hash_password(DEMO_PASSWORD),
                full_name=full_name,
                role=role,
                is_active=True,
            )
            session.add(user)
        else:
            # Re-point a stale login (e.g. after a manual delete of the centre) at the demo centre.
            user.center_id = center.id
            user.role = role
            user.is_active = True
        if role == StaffRole.teacher:
            user.teacher_id = teacher.id
        users.append(user)
    await session.flush()
    return users


async def get_or_create_student(session: AsyncSession, center: Center) -> Student:
    student = (
        await session.execute(select(Student).where(Student.center_id == center.id, Student.full_name == STUDENT_NAME))
    ).scalar_one_or_none()
    if student is None:
        student = Student(
            center_id=center.id,
            full_name=STUDENT_NAME,
            grade=STUDENT_GRADE,
            school=STUDENT_SCHOOL,
            native_language=Language.kk,
            target_subject="russian",
            notes="Демо-ученик. Родной язык — казахский, русский как второй.",
        )
        session.add(student)
        await session.flush()
    return student


async def get_or_create_parent(session: AsyncSession, center: Center, student: Student) -> Parent:
    parent = (
        await session.execute(select(Parent).where(Parent.invite_code == PARENT_INVITE_CODE))
    ).scalar_one_or_none()
    if parent is None:
        parent = Parent(
            center_id=center.id,
            full_name=PARENT_NAME,
            phone="+7 701 000 00 00",
            preferred_channel=Channel.telegram,
            preferred_language=Language.ru,
            invite_code=PARENT_INVITE_CODE,
        )
        session.add(parent)
        await session.flush()
    elif parent.center_id != center.id:
        parent.center_id = center.id
    link = (
        await session.execute(
            select(ParentStudent).where(ParentStudent.parent_id == parent.id, ParentStudent.student_id == student.id)
        )
    ).scalar_one_or_none()
    if link is None:
        session.add(ParentStudent(parent_id=parent.id, student_id=student.id, relation=PARENT_RELATION))
        await session.flush()
    return parent


def lesson_times(tz_name: str) -> list[datetime]:
    """Scheduled times for the demo lessons: ``LESSON_HOUR_LOCAL`` local time, N days ago (oldest first)."""
    tz = ZoneInfo(tz_name)
    today = datetime.now(tz).date()
    return [
        datetime.combine(today - timedelta(days=days), datetime.min.time(), tzinfo=tz).replace(hour=LESSON_HOUR_LOCAL)
        for days in sorted(LESSON_DAYS_AGO, reverse=True)
    ]


async def get_or_create_lessons(
    session: AsyncSession, center: Center, teacher: Teacher, student: Student, video: Path
) -> list[Lesson]:
    """Ensure one demo lesson per entry of :data:`LESSON_DAYS_AGO`; copy the source video into MEDIA_ROOT."""
    existing = (
        (
            await session.execute(
                select(Lesson)
                .where(
                    Lesson.center_id == center.id,
                    Lesson.student_id == student.id,
                    Lesson.source_filename == SOURCE_FILENAME,
                )
                .order_by(Lesson.scheduled_at)
            )
        )
        .scalars()
        .all()
    )
    by_date = {lesson.scheduled_at.astimezone(ZoneInfo(center.timezone)).date(): lesson for lesson in existing}
    lessons: list[Lesson] = []
    for when in lesson_times(center.timezone):
        lesson = by_date.get(when.date())
        if lesson is None:
            lesson = Lesson(
                center_id=center.id,
                teacher_id=teacher.id,
                student_id=student.id,
                subject=LESSON_SUBJECT,
                scheduled_at=when,
                source_filename=SOURCE_FILENAME,
                media_kind=MediaKind.video,
                status=LessonStatus.uploaded,
                status_message="Запись загружена (демо)",
                consent_confirmed=True,
                language_hints=["ru", "kk"],
            )
            session.add(lesson)
            await session.flush()
            log.info("demo_lesson_created", lesson_id=str(lesson.id), scheduled_at=when.isoformat())
        dst = source_path(center.id, lesson.id, SOURCE_FILENAME)
        if not dst.is_file() or dst.stat().st_size != video.stat().st_size:
            shutil.copyfile(video, dst)
        lesson.source_path = str(dst)
        lesson.consent_confirmed = True
        lessons.append(lesson)
    await session.flush()
    return lessons


# ---------------------------------------------------------------- pipeline


async def process_lessons(lesson_ids: list[uuid.UUID], *, reprocess: bool) -> dict[uuid.UUID, Lesson]:
    """Run the pipeline for each lesson (oldest first) unless it is already ``done``; return refreshed rows."""
    from app.pipeline.runner import run_lesson_pipeline

    sm = get_sessionmaker()
    results: dict[uuid.UUID, Lesson] = {}
    for lesson_id in lesson_ids:
        async with sm() as session:
            lesson = await session.get(Lesson, lesson_id)
            if lesson is None:
                raise SeedError(f"урок {lesson_id} исчез во время обработки")
            skip = lesson.status == LessonStatus.done and not reprocess
        if skip:
            log.info("demo_lesson_already_done", lesson_id=str(lesson_id))
        else:
            log.info("demo_lesson_processing", lesson_id=str(lesson_id))
            await run_lesson_pipeline(lesson_id)
        async with sm() as session:
            results[lesson_id] = await session.get(Lesson, lesson_id)  # type: ignore[assignment]
    return results


def _fixture_frame_timecodes() -> dict[int, float]:
    try:
        items = json.loads(FRAMES_FIXTURE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("demo_frames_fixture_unreadable", error=type(exc).__name__)
        return {}
    out: dict[int, float] = {}
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and "index" in item and "timestamp_sec" in item:
            out[int(item["index"])] = float(item["timestamp_sec"])
    return out


async def align_demo_timecodes(lesson_ids: list[uuid.UUID]) -> int:
    """Compressed demo video: give frames the fixture's real timecodes and the lesson its real duration.

    The 90-second ``lesson.mp4`` exists only to drive ffprobe/ffmpeg; the mock STT/LLM describe a 34-minute
    lesson. Without this step every report timecode > 90 s would highlight the last keyframe. Returns the
    number of lessons aligned (0 when the fixture is missing).
    """
    by_index = _fixture_frame_timecodes()
    if not by_index:
        return 0
    aligned = 0
    async with get_sessionmaker()() as session:
        for lesson_id in lesson_ids:
            lesson = await session.get(Lesson, lesson_id)
            if lesson is None or lesson.status != LessonStatus.done:
                continue
            frames = (await session.execute(select(Frame).where(Frame.lesson_id == lesson_id))).scalars().all()
            for frame in frames:
                if frame.index in by_index:
                    frame.timestamp_sec = by_index[frame.index]
            transcript = (
                await session.execute(select(Transcript).where(Transcript.lesson_id == lesson_id))
            ).scalar_one_or_none()
            ends = [float(seg.get("end") or 0.0) for seg in (transcript.segments if transcript else []) if seg]
            duration = max([*ends, *by_index.values()], default=0.0)
            if duration > 0:
                lesson.duration_sec = round(duration, 1)
                if transcript is not None and not transcript.duration_sec:
                    transcript.duration_sec = round(duration, 1)
            aligned += 1
        await session.commit()
    log.info("demo_timecodes_aligned", lessons=aligned)
    return aligned


# ---------------------------------------------------------------- output


def _fmt_dt(dt: datetime, tz_name: str) -> str:
    return dt.astimezone(ZoneInfo(tz_name)).strftime("%d.%m.%Y %H:%M")


def print_summary(
    *,
    center: Center,
    student: Student,
    parent: Parent,
    lessons: list[Lesson],
    magic_url: str,
    deep_link: str | None,
) -> None:
    settings = get_settings()
    width = 72
    print()
    print("=" * width)
    print("Демо-данные LessonLens готовы")
    print("=" * width)
    print(f"Центр: {center.name} ({center.city}), id={center.id}")
    print(f"Фронтенд: {settings.public_base_url}    API: {settings.api_base_url}")
    print()
    print("Вход для персонала (страница /login):")
    for email, full_name, role in STAFF:
        role_ru = {"owner": "владелец", "methodist": "методист", "teacher": "преподаватель"}[role.value]
        print(f"  {role_ru:14s} {email:20s} пароль: {DEMO_PASSWORD}   ({full_name})")
    print()
    print(f"Ученик: {student.full_name}, {student.grade} класс, id={student.id}")
    print("Уроки:")
    for lesson in lessons:
        status = lesson.status.value
        extra = f" — {lesson.status_message}" if lesson.status == LessonStatus.failed and lesson.status_message else ""
        print(f"  {_fmt_dt(lesson.scheduled_at, center.timezone)}  статус: {status}{extra}   /lessons/{lesson.id}")
    print()
    print(
        f"Родитель: {parent.full_name} ({PARENT_RELATION}), канал: {parent.preferred_channel.value}, "
        f"код приглашения: {parent.invite_code}"
    )
    print(f"Magic-ссылка в кабинет родителя (действует {settings.magic_link_ttl_hours} ч, можно открывать повторно):")
    print(f"  {magic_url}")
    print()
    print("Telegram:")
    if deep_link:
        print(f"  Deep link для родителя: {deep_link}")
        print(f"  Или отправьте боту команду: /start {parent.invite_code}")
    else:
        print("  Бот не настроен (TELEGRAM_BOT_TOKEN пуст или модуль бота недоступен).")
        print("  Чтобы показать родительский поток в Telegram: задайте TELEGRAM_BOT_TOKEN в .env, запустите API")
        print(f"  (uvicorn app.main:app) и отправьте боту /start {parent.invite_code} — бот привяжет chat_id и пришлёт")
        print("  отчёт с кнопкой в кабинет. Повторная отправка: POST /api/lessons/{id}/reports/parent/send.")
    print()
    print("Провайдеры при сидировании: STT=mock, LLM=mock, MESSAGING=" + os.environ.get("MESSAGING_PROVIDER", "mock"))
    print("Подробный сценарий демонстрации: docs/DEMO.md")
    print("=" * width)


# ---------------------------------------------------------------- main


async def seed(*, reset: bool, reprocess: bool, realtime: bool) -> int:
    """Run the whole seed; returns a process exit code."""
    settings = get_settings()
    settings.media_root.mkdir(parents=True, exist_ok=True)
    video = ensure_demo_video(realtime=realtime)
    sm = get_sessionmaker()

    async with sm() as session:
        await check_schema(session)
        if reset:
            removed = await reset_demo(session)
            print(f"Удалено демо-центров: {removed}")

    async with sm() as session:
        center = await get_or_create_center(session)
        teacher = await get_or_create_teacher(session, center)
        await get_or_create_staff(session, center, teacher)
        student = await get_or_create_student(session, center)
        parent = await get_or_create_parent(session, center, student)
        lessons = await get_or_create_lessons(session, center, teacher, student, video)
        await session.commit()
        lesson_ids = [lesson.id for lesson in lessons]
        center_id, student_id, parent_id = center.id, student.id, parent.id

    processed = await process_lessons(lesson_ids, reprocess=reprocess)
    if not realtime:
        await align_demo_timecodes(lesson_ids)
        async with sm() as session:
            for lesson_id in lesson_ids:
                refreshed = await session.get(Lesson, lesson_id)
                if refreshed is not None:
                    processed[lesson_id] = refreshed

    async with sm() as session:
        center = await session.get(Center, center_id)
        student = await session.get(Student, student_id)
        parent = await session.get(Parent, parent_id)
        assert center is not None and student is not None and parent is not None
        magic_url = await create_magic_link(session, parent)
        await session.commit()
        deep_link = deep_link_for(parent)
        ordered = [processed[i] for i in lesson_ids]
        print_summary(
            center=center, student=student, parent=parent, lessons=ordered, magic_url=magic_url, deep_link=deep_link
        )

    failed = [lesson for lesson in ordered if lesson.status != LessonStatus.done]
    if failed:
        print(
            f"ВНИМАНИЕ: {len(failed)} урок(а) не обработаны (статус failed). См. поле error в таблице lessons.",
            file=sys.stderr,
        )
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.seed_demo", description="Seed the LessonLens demo centre.")
    parser.add_argument("--reset", action="store_true", help="удалить демо-центр и его медиафайлы перед созданием")
    parser.add_argument("--reprocess", action="store_true", help="заново прогнать пайплайн для уже обработанных уроков")
    parser.add_argument("--realtime", action="store_true", help="собрать демо-видео с реальными таймкодами (~34 мин)")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(seed(reset=args.reset, reprocess=args.reprocess, realtime=args.realtime))
    except SeedError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
