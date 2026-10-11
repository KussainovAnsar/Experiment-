"""Bot command handlers called directly with fake ``Message`` objects against the test database."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from aiogram.filters import CommandObject
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import select

from app.bot import telegram as bot
from app.config import get_settings
from app.db import models
from tests.test_notifications_support import (
    BOT_USERNAME,
    PARENT_REPORT_DATA,
    Seed,
    _module_c_schema,
    mock_messaging,
    seeded,
    session,
)

__all__ = ["_module_c_schema", "mock_messaging", "seeded", "session"]


@dataclass(slots=True)
class FakeChat:
    id: int


@dataclass(slots=True)
class FakeMessage:
    """Just enough of ``aiogram.types.Message`` for the handlers: ``chat.id`` and ``answer``."""

    chat: FakeChat
    answers: list[tuple[str, Any]] = field(default_factory=list)

    async def answer(self, text: str, reply_markup: Any = None, **_: Any) -> None:
        self.answers.append((text, reply_markup))

    @property
    def texts(self) -> list[str]:
        return [t for t, _ in self.answers]


def _start(args: str | None) -> CommandObject:
    return CommandObject(prefix="/", command="start", args=args)


def _button_url(markup: Any) -> str:
    assert isinstance(markup, InlineKeyboardMarkup)
    button = markup.inline_keyboard[0][0]
    assert button.text == bot.PORTAL_BUTTON_TEXT
    assert button.url
    return button.url


async def _parent(session, parent_id: uuid.UUID) -> models.Parent:  # noqa: ANN001
    session.expire_all()
    return await session.get(models.Parent, parent_id)


async def test_start_without_code_explains(mock_messaging: None) -> None:
    msg = FakeMessage(FakeChat(1))
    await bot.handle_start(msg, _start(None))
    assert msg.texts == [bot.TEXT_START_NO_CODE]
    msg2 = FakeMessage(FakeChat(1))
    await bot.handle_start(msg2, None)
    assert msg2.texts == [bot.TEXT_START_NO_CODE]


async def test_start_binds_chat_case_insensitively(seeded: Seed, session, mock_messaging: None) -> None:  # noqa: ANN001
    new_chat = 555123
    msg = FakeMessage(FakeChat(new_chat))
    await bot.handle_start(msg, _start(f"  {seeded.code_tg_unbound.lower()} "))
    assert len(msg.answers) == 1
    text, markup = msg.answers[0]
    assert "Болат Ержанович" in text
    assert "Айдана" in text and "Сериккызы" not in text
    assert "Telegram подключён" in text
    url = _button_url(markup)
    assert url.startswith("http://testserver/p/")

    parent = await _parent(session, seeded.parent_tg_unbound_id)
    assert parent.telegram_chat_id == new_chat
    assert parent.preferred_channel is models.Channel.telegram
    links = (
        (await session.execute(select(models.MagicLink).where(models.MagicLink.parent_id == parent.id))).scalars().all()
    )
    assert len(links) == 1 and links[0].token_hash != url.rsplit("/", 1)[1]

    # repeat /start from the same chat: idempotent, "already linked" wording, a fresh link
    again = FakeMessage(FakeChat(new_chat))
    await bot.handle_start(again, _start(seeded.code_tg_unbound))
    assert "уже был подключён" in again.texts[0]
    assert _button_url(again.answers[0][1]) != url


async def test_start_chat_conflict_explains_contact_centre(seeded: Seed, session, mock_messaging: None) -> None:  # noqa: ANN001
    # chat 777001 already belongs to parent_tg; trying to claim parent_tg_unbound's code from it must fail
    msg = FakeMessage(FakeChat(seeded.chat_id))
    await bot.handle_start(msg, _start(seeded.code_tg_unbound))
    assert msg.texts == [bot.TEXT_CHAT_CONFLICT]
    parent = await _parent(session, seeded.parent_tg_unbound_id)
    assert parent.telegram_chat_id is None
    owner = await _parent(session, seeded.parent_tg_id)
    assert owner.telegram_chat_id == seeded.chat_id


async def test_start_unknown_code(seeded: Seed, mock_messaging: None) -> None:
    msg = FakeMessage(FakeChat(42))
    await bot.handle_start(msg, _start("ZZZZ9999"))
    assert len(msg.texts) == 1 and "не найден" in msg.texts[0] and "ZZZZ9999" in msg.texts[0]


async def test_progress_requires_link_then_sends_reports(seeded: Seed, mock_messaging: None) -> None:
    unlinked = FakeMessage(FakeChat(999999))
    await bot.handle_progress(unlinked)
    assert unlinked.texts == [bot.TEXT_NOT_LINKED]

    msg = FakeMessage(FakeChat(seeded.chat_id))
    await bot.handle_progress(msg)
    # two children: Айдана has a report, Арман has none; portal button only on the last message
    assert len(msg.answers) == 2
    report_text, first_markup = msg.answers[0]
    assert PARENT_REPORT_DATA["topic_plain"] in report_text
    assert "Айдана" in report_text
    assert "http://testserver/p/" in report_text
    assert first_markup is None
    none_text, last_markup = msg.answers[1]
    assert none_text == f"<b>Арман</b>: {bot.TEXT_NO_REPORTS}"
    _button_url(last_markup)


async def test_stop_disables_notifications(seeded: Seed, session, mock_messaging: None) -> None:  # noqa: ANN001
    msg = FakeMessage(FakeChat(seeded.chat_id))
    await bot.handle_stop(msg)
    assert msg.texts == [bot.TEXT_STOPPED]
    parent = await _parent(session, seeded.parent_tg_id)
    assert parent.preferred_channel is models.Channel.none

    # /start with the code re-enables the channel
    again = FakeMessage(FakeChat(seeded.chat_id))
    await bot.handle_start(again, _start(seeded.code_tg))
    parent = await _parent(session, seeded.parent_tg_id)
    assert parent.preferred_channel is models.Channel.telegram

    unlinked = FakeMessage(FakeChat(31337))
    await bot.handle_stop(unlinked)
    assert unlinked.texts == [bot.TEXT_NOT_LINKED]


async def test_help_and_unknown(mock_messaging: None) -> None:
    msg = FakeMessage(FakeChat(1))
    await bot.handle_help(msg)
    await bot.handle_unknown(msg)
    assert msg.texts == [bot.TEXT_HELP, bot.TEXT_UNKNOWN]
    assert "/progress" in bot.TEXT_HELP and "/stop" in bot.TEXT_HELP


def test_deep_link_for(seeded: Seed, mock_messaging: None, monkeypatch: pytest.MonkeyPatch) -> None:
    parent = models.Parent(center_id=seeded.center_id, full_name="x", invite_code="ABCD2345")
    assert bot.deep_link_for(parent) == f"https://t.me/{BOT_USERNAME}?start=ABCD2345"

    monkeypatch.delenv("TELEGRAM_BOT_USERNAME")
    bot.set_bot_username("@from_get_me")
    assert bot.deep_link_for(parent) == "https://t.me/from_get_me?start=ABCD2345"
    bot.set_bot_username(None)
    assert bot.deep_link_for(parent) is None  # enabled but username unknown yet

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    get_settings.cache_clear()
    bot.set_bot_username("from_get_me")
    assert bot.deep_link_for(parent) is None  # bot disabled
    bot.set_bot_username(None)


async def test_dispatcher_builds_and_bot_lifecycle_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI

    dp = bot.create_dispatcher()
    assert dp.sub_routers and dp.sub_routers[0].name == "parents"

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    get_settings.cache_clear()
    app = FastAPI()
    await bot.start_polling_background(app)
    assert not hasattr(app.state, "telegram_bot")
    await bot.stop_polling_background(app)
    assert bot.get_bot() is None
    get_settings.cache_clear()
