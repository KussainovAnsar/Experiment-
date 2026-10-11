"""What, to whom and how we send: parent lesson reports and invites, recorded in ``notifications``.

Both entry points write one ``Notification`` row per parent (``sent`` / ``failed`` / ``skipped``) and never
raise on provider problems. Nothing is committed here — the caller (pipeline runner, API handler) commits.

Dependencies on other modules are resolved lazily and by signature: ``app.services.auth.create_magic_link``
(module A; a local fallback with identical semantics exists) and
``app.pipeline.reports.render_parent_report_telegram_html`` (module B2; a minimal built-in renderer is the
fallback). The magic link itself is never stored in ``notifications.payload`` for report notifications; an
invite that could not be delivered keeps the link in its payload on purpose so staff can forward it by hand.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import secrets
import uuid
from collections.abc import Callable, Coroutine, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import (
    Center,
    Channel,
    Lesson,
    MagicLink,
    Notification,
    NotificationStatus,
    Parent,
    ParentStudent,
    Report,
    ReportKind,
    Student,
)
from app.logging import get_logger
from app.providers.messaging.base import InlineButton, Messenger, ProviderNotConfigured, SendResult
from app.providers.messaging.registry import get_messenger

log = get_logger("app.services.notifications")

PORTAL_BUTTON_TEXT = "Открыть кабинет"
PORTAL_FOOTER_TEXT = "Полный отчёт — в кабинете родителя"
TELEGRAM_MAX_CHARS = 1500

HtmlBuilder = Callable[[Parent, str | None], Coroutine[Any, Any, str]]


# ---------------------------------------------------------------- small helpers (also used by the bot)


def first_name(full_name: str | None) -> str | None:
    """First token of a full name (``«Айдана Сериккызы»`` → ``Айдана``); ``None`` when empty."""
    if not full_name:
        return None
    parts = full_name.strip().split()
    return parts[0] if parts else None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


async def _local_create_magic_link(session: AsyncSession, parent: Parent, *, purpose: str = "portal") -> str:
    """Fallback with the same semantics as ``app.services.auth.create_magic_link`` (sha256 hash stored)."""
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    session.add(
        MagicLink(
            parent_id=parent.id,
            token_hash=_hash_token(token),
            purpose=purpose,
            expires_at=datetime.now(UTC) + timedelta(hours=settings.magic_link_ttl_hours),
        )
    )
    await session.flush()
    return f"{settings.public_base_url.rstrip('/')}/p/{token}"


async def portal_link_for(session: AsyncSession, parent: Parent) -> str:
    """Issue a fresh portal magic link for ``parent`` (module A implementation when available)."""
    try:
        from app.services.auth import create_magic_link
    except ImportError:  # module A not present
        return await _local_create_magic_link(session, parent)
    return await create_magic_link(session, parent, purpose="portal")


def deep_link_for_parent(parent: Parent) -> str | None:
    """Telegram deep link for the parent's invite code, or ``None`` when the bot is disabled/unknown."""
    try:
        from app.bot.telegram import deep_link_for
    except ImportError:  # pragma: no cover - bot module is part of this package
        return None
    return deep_link_for(parent)


def _fallback_render_parent_report(report_data: dict, portal_url: str | None) -> str:
    """Minimal aiogram-safe HTML from ``report.data`` when module B2's renderer is unavailable (≤ 1500 chars)."""
    d = report_data
    esc = html_lib.escape
    header = esc(str(d.get("subject") or "Урок"))
    if d.get("child_first_name"):
        header = f"{esc(str(d['child_first_name']))} — {header}"
    if d.get("date"):
        header += f", {esc(str(d['date']))}"
    lines = [f"<b>{header}</b>"]
    if d.get("topic_plain"):
        lines.append(f"<i>Тема:</i> {esc(str(d['topic_plain']))}")

    def section(title: str, items: Sequence[str] | None) -> None:
        items = [i for i in (items or []) if str(i).strip()]
        if items:
            lines.append("")
            lines.append(f"<b>{title}</b>")
            lines.extend(f"• {esc(str(i))}" for i in items)

    section("Получилось самостоятельно", d.get("what_child_did_well"))
    section("Где были ошибки", d.get("what_was_hard"))
    section("Что повторить дома", d.get("what_to_repeat"))
    if d.get("encouragement"):
        lines.append("")
        lines.append(f"<i>{esc(str(d['encouragement']))}</i>")
    if portal_url:
        footer = f'<a href="{html_lib.escape(portal_url, quote=True)}">{PORTAL_FOOTER_TEXT}</a>'
    else:
        footer = PORTAL_FOOTER_TEXT
    body = "\n".join(lines)
    budget = TELEGRAM_MAX_CHARS - len(footer) - 2
    if len(body) > budget:
        body = body[: budget - 1].rstrip() + "…"
    return f"{body}\n\n{footer}"


def render_report_html(report_data: dict, portal_url: str | None) -> str:
    """Render the parent report with module B2's renderer, falling back to the built-in one."""
    try:
        from app.pipeline.reports import render_parent_report_telegram_html
    except ImportError:
        return _fallback_render_parent_report(report_data, portal_url)
    return render_parent_report_telegram_html(report_data, portal_url)


async def latest_parent_report(session: AsyncSession, lesson_id: uuid.UUID) -> Report | None:
    """Highest-version parent report of a lesson, or ``None``."""
    result = await session.execute(
        select(Report)
        .where(Report.lesson_id == lesson_id, Report.kind == ReportKind.parent)
        .order_by(Report.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def latest_parent_report_for_student(session: AsyncSession, student_id: uuid.UUID) -> Report | None:
    """Parent report of the student's most recent lesson that has one (by ``scheduled_at``), or ``None``."""
    result = await session.execute(
        select(Report)
        .join(Lesson, Lesson.id == Report.lesson_id)
        .where(Lesson.student_id == student_id, Report.kind == ReportKind.parent)
        .order_by(Lesson.scheduled_at.desc(), Report.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def linked_students(session: AsyncSession, parent_id: uuid.UUID) -> list[Student]:
    """Students linked to the parent, ordered by name."""
    result = await session.execute(
        select(Student)
        .join(ParentStudent, ParentStudent.student_id == Student.id)
        .where(ParentStudent.parent_id == parent_id)
        .order_by(Student.full_name)
    )
    return list(result.scalars().all())


async def linked_parents(session: AsyncSession, student_id: uuid.UUID) -> list[Parent]:
    """Parents linked to the student."""
    result = await session.execute(
        select(Parent)
        .join(ParentStudent, ParentStudent.parent_id == Parent.id)
        .where(ParentStudent.student_id == student_id)
        .order_by(Parent.created_at)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------- delivery core


def _chat_ref_for(parent: Parent, channel: Channel) -> tuple[str | None, str | None]:
    """``(chat_ref, skip_reason)`` for the channel; ``skip_reason`` is set when nothing can be sent."""
    if channel == Channel.telegram:
        if parent.telegram_chat_id is None:
            return None, "telegram_not_linked"
        return str(parent.telegram_chat_id), None
    if channel == Channel.whatsapp:
        phone = parent.whatsapp_phone or parent.phone
        if not phone:
            return None, "whatsapp_phone_missing"
        return phone, None
    return None, "channel_none"


_SKIP_MESSAGES = {
    "telegram_not_linked": "Родитель ещё не открыл Telegram-бот по пригласительной ссылке",
    "whatsapp_phone_missing": "У родителя не указан номер WhatsApp",
    "channel_none": "Родитель отключил уведомления (канал: none)",
}


async def _deliver(
    session: AsyncSession,
    parent: Parent,
    *,
    lesson_id: uuid.UUID | None,
    build_html: HtmlBuilder,
    kind: str,
    extra_payload: dict[str, Any] | None = None,
    keep_link_when_skipped: bool = False,
) -> Notification:
    """Send one message to ``parent`` via its preferred channel and record the outcome. Never raises."""
    channel = parent.preferred_channel
    if not isinstance(channel, Channel):
        channel = Channel(channel)
    deep_link = deep_link_for_parent(parent)
    notification = Notification(
        center_id=parent.center_id,
        parent_id=parent.id,
        lesson_id=lesson_id,
        channel=channel,
        status=NotificationStatus.pending,
        payload={"kind": kind, "channel": channel.value, "deep_link": deep_link, **(extra_payload or {})},
    )
    session.add(notification)

    chat_ref, skip_reason = _chat_ref_for(parent, channel)
    if skip_reason is not None:
        notification.status = NotificationStatus.skipped
        notification.error = _SKIP_MESSAGES[skip_reason]
        notification.payload = {**notification.payload, "reason": skip_reason, "has_portal_link": False}
        if keep_link_when_skipped:
            try:
                notification.payload["magic_url"] = await portal_link_for(session, parent)
                notification.payload["has_portal_link"] = True
            except Exception as exc:  # pragma: no cover - defensive; DB errors would surface at flush anyway
                log.warning("magic_link_failed", parent_id=str(parent.id), error=str(exc)[:200])
        await session.flush()
        log.info("notification_skipped", parent_id=str(parent.id), lesson_id=str(lesson_id), reason=skip_reason)
        return notification

    settings = get_settings()
    try:
        messenger: Messenger = get_messenger(settings, channel)
    except ProviderNotConfigured as exc:
        notification.status = NotificationStatus.failed
        notification.error = str(exc)
        notification.payload = {**notification.payload, "has_portal_link": False, "reason": "provider_not_configured"}
        await session.flush()
        log.warning("notification_provider_not_configured", parent_id=str(parent.id), channel=channel.value)
        return notification

    try:
        portal_url = await portal_link_for(session, parent)
        html = await build_html(parent, portal_url)
        result: SendResult = await messenger.send_html(
            chat_ref, html, button=InlineButton(PORTAL_BUTTON_TEXT, portal_url)
        )
    except Exception as exc:  # provider bug or unexpected error: record, never propagate
        log.exception("notification_send_crashed", parent_id=str(parent.id), channel=channel.value)
        result = SendResult.failure(f"{type(exc).__name__}: {str(exc)[:300]}")

    provider_name = getattr(messenger, "name", None)
    notification.payload = {**notification.payload, "has_portal_link": True, "provider": provider_name}
    if result.ok:
        notification.status = NotificationStatus.sent
        notification.provider_message_id = result.provider_message_id
        notification.sent_at = datetime.now(UTC)
        log.info("notification_sent", parent_id=str(parent.id), lesson_id=str(lesson_id), channel=channel.value)
    else:
        notification.status = NotificationStatus.failed
        notification.error = result.error
        log.warning(
            "notification_failed",
            parent_id=str(parent.id),
            lesson_id=str(lesson_id),
            channel=channel.value,
            error=result.error,
        )
    await session.flush()
    return notification


# ---------------------------------------------------------------- public API


async def notify_parents_for_lesson(session: AsyncSession, lesson_id: uuid.UUID) -> list[Notification]:
    """Send the latest parent report of ``lesson_id`` to every linked parent via their preferred channel.

    Each parent gets a fresh portal magic link inside the message (and as an inline button). Parents without
    a bound Telegram chat (or with ``preferred_channel=none``) get a ``skipped`` row whose payload carries the
    Telegram deep link so staff can invite them. Provider failures become ``failed`` rows. Returns the
    notifications (flushed, not committed); an empty list when the lesson or its parent report does not exist.
    """
    lesson = await session.get(Lesson, lesson_id)
    if lesson is None:
        log.warning("notify_lesson_missing", lesson_id=str(lesson_id))
        return []
    report = await latest_parent_report(session, lesson_id)
    if report is None:
        log.warning("notify_no_parent_report", lesson_id=str(lesson_id))
        return []
    parents = await linked_parents(session, lesson.student_id)
    if not parents:
        log.info("notify_no_parents", lesson_id=str(lesson_id))
        return []

    report_data = report.data or {}

    async def build(parent: Parent, portal_url: str | None) -> str:
        return render_report_html(report_data, portal_url)

    notifications: list[Notification] = []
    for parent in parents:
        notifications.append(
            await _deliver(
                session,
                parent,
                lesson_id=lesson.id,
                build_html=build,
                kind="lesson_report",
                extra_payload={"report_id": str(report.id), "report_version": report.version},
            )
        )
    return notifications


def render_invite_html(
    *,
    parent_name: str | None,
    center_name: str | None,
    child_first_names: Sequence[str],
    portal_url: str | None,
    deep_link: str | None,
    link_ttl_hours: int,
    telegram_linked: bool,
) -> str:
    """Welcome message explaining what the parent will receive, with the portal link and the bot deep link."""
    esc = html_lib.escape
    greeting = f"Здравствуйте, {esc(parent_name)}!" if parent_name else "Здравствуйте!"
    centre = f"Центр «{esc(center_name)}»" if center_name else "Ваш образовательный центр"
    lines = [
        f"<b>{greeting}</b>",
        f"{centre} подключил вас к LessonLens. После каждого занятия вы будете получать короткий отчёт: "
        "тема урока, что получилось у ребёнка самостоятельно, где были трудности и что стоит повторить дома.",
    ]
    if child_first_names:
        label = "Ребёнок" if len(child_first_names) == 1 else "Дети"
        lines.append(f"<b>{label}:</b> {esc(', '.join(child_first_names))}")
    if portal_url:
        lines.append("")
        lines.append(
            f'<a href="{esc(portal_url, quote=True)}">Открыть кабинет родителя</a> — ссылка действует '
            f"{link_ttl_hours} ч., новую можно получить в центре или командой /progress в боте."
        )
    if deep_link and not telegram_linked:
        lines.append("")
        lines.append(
            f'Чтобы получать отчёты в Telegram, откройте бот по ссылке: <a href="{esc(deep_link, quote=True)}">'
            f"{esc(deep_link)}</a> и нажмите «Start»."
        )
    lines.append("")
    lines.append("<i>Отчёты содержат только имя ребёнка и учебные наблюдения, без оценок и диагнозов.</i>")
    return "\n".join(lines)


async def send_parent_invite(session: AsyncSession, parent_id: uuid.UUID) -> Notification:
    """Send a welcome message with a portal magic link and the Telegram deep link via the preferred channel.

    When the parent prefers Telegram but has not opened the bot yet (``telegram_chat_id`` unknown) — or has
    ``preferred_channel=none`` — the row is ``skipped`` and its payload carries ``deep_link`` and ``magic_url``
    so staff can forward them manually. Flushed, not committed. Raises ``ValueError`` only for an unknown parent.
    """
    parent = await session.get(Parent, parent_id)
    if parent is None:
        raise ValueError("parent not found")
    center = await session.get(Center, parent.center_id)
    students = await linked_students(session, parent.id)
    names = [n for n in (first_name(s.full_name) for s in students) if n]
    settings = get_settings()

    async def build(p: Parent, portal_url: str | None) -> str:
        return render_invite_html(
            parent_name=p.full_name,
            center_name=center.name if center else None,
            child_first_names=names,
            portal_url=portal_url,
            deep_link=deep_link_for_parent(p),
            link_ttl_hours=settings.magic_link_ttl_hours,
            telegram_linked=p.telegram_chat_id is not None,
        )

    return await _deliver(
        session,
        parent,
        lesson_id=None,
        build_html=build,
        kind="invite",
        extra_payload={"students_count": len(students)},
        keep_link_when_skipped=True,
    )
