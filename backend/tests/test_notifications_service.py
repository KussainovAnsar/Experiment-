"""notify_parents_for_lesson / send_parent_invite against the test database with the mock messenger."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import models
from app.providers.messaging import mock
from app.services import notifications as svc
from tests.test_notifications_support import (
    BOT_USERNAME,
    PARENT_REPORT_DATA,
    Seed,
    _module_c_schema,
    mock_messaging,
    seeded,
    session,
)

__all__ = ["_module_c_schema", "mock_messaging", "seeded", "session"]  # fixtures re-exported for pytest discovery


def _by_parent(items: list[models.Notification]) -> dict[uuid.UUID, models.Notification]:
    return {n.parent_id: n for n in items}


async def _magic_links(session: AsyncSession, parent_id: uuid.UUID) -> list[models.MagicLink]:
    rows = await session.execute(select(models.MagicLink).where(models.MagicLink.parent_id == parent_id))
    return list(rows.scalars().all())


async def test_notify_parents_sent_skipped_and_none(seeded: Seed, session: AsyncSession, mock_messaging: None) -> None:
    result = await svc.notify_parents_for_lesson(session, seeded.lesson_id)
    await session.commit()
    assert len(result) == 4
    by_parent = _by_parent(result)

    sent = by_parent[seeded.parent_tg_id]
    assert sent.status is models.NotificationStatus.sent
    assert sent.channel is models.Channel.telegram
    assert sent.lesson_id == seeded.lesson_id and sent.center_id == seeded.center_id
    assert sent.provider_message_id and sent.provider_message_id.startswith("mock-")
    assert sent.sent_at is not None
    assert sent.payload["has_portal_link"] is True
    assert sent.payload["kind"] == "lesson_report"
    assert sent.payload["report_id"] == str(seeded.report_id)
    assert sent.payload["deep_link"] == f"https://t.me/{BOT_USERNAME}?start={seeded.code_tg}"

    # exactly one message left for the bound Telegram parent, with the report text and a portal link
    tg_messages = [m for m in mock.sent if m.chat_ref == str(seeded.chat_id)]
    assert len(tg_messages) == 1
    msg = tg_messages[0]
    assert msg.is_html
    assert "Айдана" in msg.text
    assert PARENT_REPORT_DATA["topic_plain"] in msg.text
    assert PARENT_REPORT_DATA["what_to_repeat"][0] in msg.text
    assert "http://testserver/p/" in msg.text
    assert msg.button is not None and msg.button.url.startswith("http://testserver/p/")
    assert msg.button.text == svc.PORTAL_BUTTON_TEXT
    assert len(msg.text) <= 1500
    token = msg.button.url.rsplit("/", 1)[1]
    # the magic link is stored hashed and never copied into the notification payload
    links = await _magic_links(session, seeded.parent_tg_id)
    assert len(links) == 1 and links[0].token_hash != token and len(links[0].token_hash) == 64
    assert token not in json.dumps(sent.payload) and "magic_url" not in sent.payload

    skipped = by_parent[seeded.parent_tg_unbound_id]
    assert skipped.status is models.NotificationStatus.skipped
    assert skipped.payload["reason"] == "telegram_not_linked"
    assert skipped.payload["deep_link"] == f"https://t.me/{BOT_USERNAME}?start={seeded.code_tg_unbound}"
    assert skipped.payload["has_portal_link"] is False
    assert skipped.error and "Telegram" in skipped.error
    assert await _magic_links(session, seeded.parent_tg_unbound_id) == []

    none = by_parent[seeded.parent_none_id]
    assert none.status is models.NotificationStatus.skipped
    assert none.payload["reason"] == "channel_none"

    # whatsapp parent goes through the mock too (MESSAGING_PROVIDER=mock)
    wa = by_parent[seeded.parent_wa_id]
    assert wa.status is models.NotificationStatus.sent
    assert wa.channel is models.Channel.whatsapp
    assert any(m.channel == "whatsapp" for m in mock.sent)

    stored = (
        (await session.execute(select(models.Notification).where(models.Notification.lesson_id == seeded.lesson_id)))
        .scalars()
        .all()
    )
    assert len(stored) == 4


async def test_whatsapp_disabled_is_failed_with_clear_error(
    seeded: Seed, session: AsyncSession, mock_messaging: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MESSAGING_PROVIDER", raising=False)
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")  # telegram parents fall into provider-not-configured too
    get_settings.cache_clear()

    result = await svc.notify_parents_for_lesson(session, seeded.lesson_id)
    await session.commit()
    by_parent = _by_parent(result)
    wa = by_parent[seeded.parent_wa_id]
    assert wa.status is models.NotificationStatus.failed
    assert wa.error and "WHATSAPP_ENABLED" in wa.error and "MESSAGING.md" in wa.error
    assert wa.payload["reason"] == "provider_not_configured"
    assert wa.payload["deep_link"] is None  # bot disabled → no deep link

    tg = by_parent[seeded.parent_tg_id]
    assert tg.status is models.NotificationStatus.failed
    assert tg.error and "TELEGRAM_BOT_TOKEN" in tg.error
    assert mock.sent == []


async def test_provider_failure_is_recorded_not_raised(
    seeded: Seed, session: AsyncSession, mock_messaging: None
) -> None:
    mock.fail_with = "Telegram API 403: bot was blocked by the user"
    result = await svc.notify_parents_for_lesson(session, seeded.lesson_id)
    await session.commit()
    tg = _by_parent(result)[seeded.parent_tg_id]
    assert tg.status is models.NotificationStatus.failed
    assert tg.error == "Telegram API 403: bot was blocked by the user"
    assert tg.provider_message_id is None and tg.sent_at is None


async def test_notify_unknown_lesson_or_missing_report(
    seeded: Seed, session: AsyncSession, mock_messaging: None
) -> None:
    assert await svc.notify_parents_for_lesson(session, uuid.uuid4()) == []
    lesson = models.Lesson(
        center_id=seeded.center_id,
        teacher_id=seeded.teacher_id,
        student_id=seeded.student_id,
        scheduled_at=(await session.get(models.Lesson, seeded.lesson_id)).scheduled_at,
        consent_confirmed=True,
    )
    session.add(lesson)
    await session.commit()
    assert await svc.notify_parents_for_lesson(session, lesson.id) == []
    assert mock.sent == []


async def test_notify_uses_latest_report_version(seeded: Seed, session: AsyncSession, mock_messaging: None) -> None:
    session.add(
        models.Report(
            lesson_id=seeded.lesson_id,
            kind=models.ReportKind.parent,
            data={**PARENT_REPORT_DATA, "topic_plain": "ОБНОВЛЁННАЯ ТЕМА v2"},
            markdown="",
            version=2,
        )
    )
    await session.commit()
    result = await svc.notify_parents_for_lesson(session, seeded.lesson_id)
    await session.commit()
    tg = _by_parent(result)[seeded.parent_tg_id]
    assert tg.payload["report_version"] == 2
    assert "ОБНОВЛЁННАЯ ТЕМА v2" in mock.sent[0].text


async def test_send_parent_invite_telegram_unbound_is_skipped_with_links(
    seeded: Seed, session: AsyncSession, mock_messaging: None
) -> None:
    n = await svc.send_parent_invite(session, seeded.parent_tg_unbound_id)
    await session.commit()
    assert n.status is models.NotificationStatus.skipped
    assert n.lesson_id is None and n.parent_id == seeded.parent_tg_unbound_id
    assert n.payload["kind"] == "invite"
    assert n.payload["reason"] == "telegram_not_linked"
    assert n.payload["deep_link"] == f"https://t.me/{BOT_USERNAME}?start={seeded.code_tg_unbound}"
    assert n.payload["magic_url"].startswith("http://testserver/p/")
    assert n.payload["has_portal_link"] is True
    assert len(await _magic_links(session, seeded.parent_tg_unbound_id)) == 1
    assert mock.sent == []


async def test_send_parent_invite_telegram_bound_is_sent(
    seeded: Seed, session: AsyncSession, mock_messaging: None
) -> None:
    n = await svc.send_parent_invite(session, seeded.parent_tg_id)
    await session.commit()
    assert n.status is models.NotificationStatus.sent
    assert n.payload["students_count"] == 2
    assert "magic_url" not in n.payload
    msg = mock.sent[0]
    assert msg.chat_ref == str(seeded.chat_id)
    assert "Гульнара Сериковна" in msg.text
    assert "Айдана" in msg.text and "Арман" in msg.text
    assert "Сериккызы" not in msg.text  # first names only
    assert "Тестовый центр C" in msg.text
    assert "http://testserver/p/" in msg.text
    assert "t.me/" not in msg.text  # already bound → no deep link in the greeting
    assert msg.button is not None and msg.button.text == svc.PORTAL_BUTTON_TEXT


async def test_send_parent_invite_channel_none_and_unknown_parent(
    seeded: Seed, session: AsyncSession, mock_messaging: None
) -> None:
    n = await svc.send_parent_invite(session, seeded.parent_none_id)
    await session.commit()
    assert n.status is models.NotificationStatus.skipped
    assert n.payload["reason"] == "channel_none"
    assert n.payload["magic_url"].startswith("http://testserver/p/")
    assert n.payload["deep_link"]
    with pytest.raises(ValueError):
        await svc.send_parent_invite(session, uuid.uuid4())


def test_fallback_renderer_is_short_and_safe() -> None:
    data = {**PARENT_REPORT_DATA, "what_to_repeat": ["<script>alert(1)</script>"] * 40}
    html = svc._fallback_render_parent_report(data, "http://testserver/p/abc?x=1&y=2")
    assert len(html) <= 1500
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert html.endswith(f'<a href="http://testserver/p/abc?x=1&amp;y=2">{svc.PORTAL_FOOTER_TEXT}</a>')
    assert html.startswith("<b>Айдана — Русский язык, 11 октября 2026</b>")
    assert svc._fallback_render_parent_report({"subject": "Урок"}, None).endswith(svc.PORTAL_FOOTER_TEXT)


def test_first_name() -> None:
    assert svc.first_name("Айдана Сериккызы") == "Айдана"
    assert svc.first_name("  ") is None and svc.first_name(None) is None
