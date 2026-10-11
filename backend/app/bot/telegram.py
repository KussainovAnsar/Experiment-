"""aiogram 3 bot for parents.

Commands (all replies in Russian):

* ``/start <invite_code>`` — find the parent by invite code (case-insensitive), bind ``telegram_chat_id``,
  greet with the linked children's first names and a fresh portal magic link (inline URL button).
* ``/start`` without a code — explain how to get the invite from the centre.
* ``/progress`` — latest parent report per linked child (re-rendered with a fresh portal link) or «Отчётов пока нет».
* ``/help`` — command list. ``/stop`` — ``preferred_channel = none``.

Handlers open their own DB sessions via :func:`app.db.session.get_sessionmaker` and are plain coroutines, so
tests can call them with fake ``Message`` objects. The bot username (for deep links) is fetched once with
``getMe`` at startup and cached in the module; ``TELEGRAM_BOT_USERNAME`` overrides it (useful in webhook mode
and tests). Polling runs as a background asyncio task managed by :func:`start_polling_background` /
:func:`stop_polling_background`.
"""

from __future__ import annotations

import asyncio
import contextlib
import html as html_lib
import os
from collections.abc import Sequence
from typing import Any

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Channel, Parent
from app.db.session import get_sessionmaker
from app.logging import get_logger
from app.services.notifications import (
    PORTAL_BUTTON_TEXT,
    first_name,
    latest_parent_report_for_student,
    linked_students,
    portal_link_for,
    render_report_html,
)

log = get_logger("app.bot.telegram")

BOT_USERNAME_ENV = "TELEGRAM_BOT_USERNAME"

# Module-level cache: resolved once per process (getMe) or taken from the environment.
_bot_username: str | None = None
_bot: Bot | None = None
_dispatcher: Dispatcher | None = None
_polling_task: asyncio.Task[None] | None = None

router = Router(name="parents")


# ---------------------------------------------------------------- texts (Russian, respectful)

TEXT_START_NO_CODE = (
    "Здравствуйте! Это бот LessonLens: после каждого занятия он присылает короткий отчёт о том, как прошёл "
    "урок у вашего ребёнка.\n\n"
    "Чтобы подключиться, нужен пригласительный код от вашего образовательного центра. Попросите код у "
    "администратора и отправьте команду вида:\n<code>/start ABCD2345</code>\n\n"
    "Если центр прислал вам ссылку на бот — просто откройте её и нажмите «Start»."
)
TEXT_CODE_NOT_FOUND = (
    "Код <b>{code}</b> не найден. Проверьте, пожалуйста, написание (код состоит из 8 латинских букв и цифр) "
    "или уточните код у администратора центра."
)
TEXT_CHAT_CONFLICT = (
    "Этот Telegram-аккаунт уже привязан к другому родителю. Чтобы мы не перепутали отчёты, обратитесь, "
    "пожалуйста, в ваш образовательный центр — администратор поможет переназначить привязку."
)
TEXT_NOT_LINKED = (
    "Ваш Telegram пока не привязан к кабинету родителя. Отправьте команду <code>/start КОД</code> с "
    "пригласительным кодом от центра."
)
TEXT_NO_REPORTS = "Отчётов пока нет"
TEXT_STOPPED = (
    "Вы отключили уведомления. Отчёты по-прежнему доступны в кабинете родителя. Чтобы снова получать их в "
    "Telegram, отправьте /start."
)
TEXT_HELP = (
    "<b>Команды бота LessonLens</b>\n"
    "/start КОД — подключиться по пригласительному коду от центра\n"
    "/progress — последние отчёты по занятиям ваших детей и ссылка в кабинет\n"
    "/stop — отключить уведомления в Telegram\n"
    "/help — эта справка\n\n"
    "Отчёты содержат только имя ребёнка и учебные наблюдения — без оценок преподавателя и без «диагнозов». "
    "Ссылка в кабинет действует ограниченное время; новую всегда можно получить командой /progress."
)
TEXT_UNKNOWN = "Я понимаю только команды. Отправьте /help, чтобы увидеть список."


# ---------------------------------------------------------------- bot / username / deep links


def _configured_username() -> str | None:
    value = os.environ.get(BOT_USERNAME_ENV, "").strip().lstrip("@")
    return value or None


def bot_username() -> str | None:
    """Cached bot username: ``TELEGRAM_BOT_USERNAME`` env override, else the value fetched by ``getMe``."""
    return _configured_username() or _bot_username


def set_bot_username(username: str | None) -> None:
    """Override the cached username (tests, or after a manual ``getMe``)."""
    global _bot_username
    _bot_username = username.lstrip("@") if username else None


def deep_link_for(parent: Parent) -> str | None:
    """``https://t.me/<bot_username>?start=<invite_code>``; ``None`` when the bot is disabled or username unknown."""
    settings = get_settings()
    if not settings.telegram_enabled:
        return None
    username = bot_username()
    if not username or not parent.invite_code:
        return None
    return f"https://t.me/{username}?start={parent.invite_code}"


def get_bot() -> Bot | None:
    """Process-wide aiogram ``Bot`` (HTML parse mode) or ``None`` when ``TELEGRAM_BOT_TOKEN`` is empty."""
    global _bot
    settings = get_settings()
    if not settings.telegram_enabled:
        return None
    if _bot is None or _bot.token != settings.telegram_bot_token:
        _bot = Bot(
            token=settings.telegram_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
        )
    return _bot


async def ensure_bot_username(bot: Bot | None = None) -> str | None:
    """Resolve and cache the bot username via ``getMe`` (once); returns it or ``None`` on failure/disabled."""
    global _bot_username
    configured = _configured_username()
    if configured:
        return configured
    if _bot_username:
        return _bot_username
    bot = bot or get_bot()
    if bot is None:
        return None
    try:
        me = await bot.get_me()
    except Exception as exc:  # network / bad token — deep links degrade to None, bot keeps working
        log.warning("telegram_get_me_failed", error=type(exc).__name__)
        return None
    _bot_username = me.username
    log.info("telegram_bot_ready", username=me.username)
    return _bot_username


def create_dispatcher() -> Dispatcher:
    """Build a Dispatcher with the parent router attached."""
    dp = Dispatcher()
    dp.include_router(router)
    return dp


def get_dispatcher() -> Dispatcher:
    """Process-wide Dispatcher (created lazily)."""
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = create_dispatcher()
    return _dispatcher


def portal_keyboard(url: str) -> InlineKeyboardMarkup:
    """Single-button inline keyboard opening the parent portal."""
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=PORTAL_BUTTON_TEXT, url=url)]])


# ---------------------------------------------------------------- DB helpers


async def find_parent_by_invite_code(session: AsyncSession, code: str) -> Parent | None:
    """Case-insensitive lookup by ``invite_code``."""
    normalized = code.strip().upper()
    if not normalized:
        return None
    result = await session.execute(select(Parent).where(func.upper(Parent.invite_code) == normalized))
    return result.scalar_one_or_none()


async def find_parent_by_chat_id(session: AsyncSession, chat_id: int) -> Parent | None:
    """Parent bound to this Telegram chat, or ``None``."""
    result = await session.execute(select(Parent).where(Parent.telegram_chat_id == chat_id))
    return result.scalar_one_or_none()


def _children_line(names: Sequence[str]) -> str:
    if not names:
        return "К вашему аккаунту пока не привязан ни один ученик — центр добавит ребёнка в ближайшее время."
    label = "Ваш ребёнок" if len(names) == 1 else "Ваши дети"
    return f"{label}: <b>{html_lib.escape(', '.join(names))}</b>."


def _greeting(parent: Parent, names: Sequence[str], *, already_linked: bool) -> str:
    hello = f"Здравствуйте, {html_lib.escape(parent.full_name)}!" if parent.full_name else "Здравствуйте!"
    status = (
        "Ваш Telegram уже был подключён — всё в порядке."
        if already_linked
        else "Telegram подключён: после каждого занятия сюда будет приходить короткий отчёт."
    )
    return (
        f"<b>{hello}</b>\n{status}\n\n{_children_line(names)}\n\n"
        "В кабинете родителя — полные отчёты по урокам и динамика по темам. Ссылка ниже действует "
        "ограниченное время; новую всегда можно получить командой /progress."
    )


# ---------------------------------------------------------------- handlers


@router.message(CommandStart())
async def handle_start(message: Message, command: CommandObject | None = None) -> None:
    """``/start [invite_code]`` — bind the chat to a parent and send the portal link."""
    code = (command.args if command is not None else "") or ""
    code = code.strip().upper()
    if not code:
        await message.answer(TEXT_START_NO_CODE)
        return
    chat_id = message.chat.id
    async with get_sessionmaker()() as session:
        parent = await find_parent_by_invite_code(session, code)
        if parent is None:
            log.info("bot_start_unknown_code", chat_id=chat_id)
            await message.answer(TEXT_CODE_NOT_FOUND.format(code=html_lib.escape(code)))
            return
        already_linked = parent.telegram_chat_id == chat_id
        if not already_linked:
            other = await find_parent_by_chat_id(session, chat_id)
            if other is not None and other.id != parent.id:
                log.warning("bot_start_chat_conflict", parent_id=str(parent.id), other_parent_id=str(other.id))
                await message.answer(TEXT_CHAT_CONFLICT)
                return
            parent.telegram_chat_id = chat_id
            try:
                await session.flush()
            except IntegrityError:  # race: the same chat was bound to another parent concurrently
                await session.rollback()
                log.warning("bot_start_chat_conflict_race", parent_id=str(parent.id))
                await message.answer(TEXT_CHAT_CONFLICT)
                return
        if parent.preferred_channel == Channel.none:  # /start after /stop re-enables Telegram notifications
            parent.preferred_channel = Channel.telegram
        students = await linked_students(session, parent.id)
        names = [n for n in (first_name(s.full_name) for s in students) if n]
        portal_url = await portal_link_for(session, parent)
        await session.commit()
    log.info("bot_start_linked", parent_id=str(parent.id), already_linked=already_linked)
    text = _greeting(parent, names, already_linked=already_linked)
    await message.answer(text, reply_markup=portal_keyboard(portal_url))


@router.message(Command("progress"))
async def handle_progress(message: Message) -> None:
    """``/progress`` — latest parent report per child with a fresh portal link."""
    chat_id = message.chat.id
    async with get_sessionmaker()() as session:
        parent = await find_parent_by_chat_id(session, chat_id)
        if parent is None:
            await message.answer(TEXT_NOT_LINKED)
            return
        students = await linked_students(session, parent.id)
        if not students:
            await message.answer(_children_line([]))
            return
        portal_url = await portal_link_for(session, parent)
        messages: list[str] = []
        for student in students:
            report = await latest_parent_report_for_student(session, student.id)
            if report is None:
                name = first_name(student.full_name)
                messages.append(f"<b>{html_lib.escape(name)}</b>: {TEXT_NO_REPORTS}" if name else TEXT_NO_REPORTS)
                continue
            messages.append(render_report_html(report.data or {}, portal_url))
        await session.commit()
    log.info("bot_progress", parent_id=str(parent.id), students=len(students))
    for index, text in enumerate(messages):
        is_last = index == len(messages) - 1
        await message.answer(text, reply_markup=portal_keyboard(portal_url) if is_last else None)


@router.message(Command("help"))
async def handle_help(message: Message) -> None:
    """``/help`` — command list."""
    await message.answer(TEXT_HELP)


@router.message(Command("stop"))
async def handle_stop(message: Message) -> None:
    """``/stop`` — disable notifications for the bound parent."""
    chat_id = message.chat.id
    async with get_sessionmaker()() as session:
        parent = await find_parent_by_chat_id(session, chat_id)
        if parent is None:
            await message.answer(TEXT_NOT_LINKED)
            return
        parent.preferred_channel = Channel.none
        await session.commit()
    log.info("bot_stop", parent_id=str(parent.id))
    await message.answer(TEXT_STOPPED)


@router.message()
async def handle_unknown(message: Message) -> None:
    """Anything else — point to /help."""
    await message.answer(TEXT_UNKNOWN)


# ---------------------------------------------------------------- lifecycle


def _webhook_url(settings: Any) -> str | None:
    base = (settings.telegram_webhook_url or "").strip()
    if not base or not settings.telegram_webhook_secret:
        return None
    if "/api/telegram/webhook/" in base:
        return base
    return f"{base.rstrip('/')}/api/telegram/webhook/{settings.telegram_webhook_secret}"


async def start_polling_background(app: FastAPI) -> None:
    """Start long polling as a background task (no-op when the token is empty or ``TELEGRAM_MODE != polling``).

    With a token set the bot username is resolved via ``getMe`` in both modes so deep links work; in webhook
    mode the webhook is registered when ``TELEGRAM_WEBHOOK_URL`` and ``TELEGRAM_WEBHOOK_SECRET`` are set.
    """
    global _polling_task
    settings = get_settings()
    if not settings.telegram_enabled:
        log.info("telegram_disabled")
        return
    bot = get_bot()
    if bot is None:
        return
    await ensure_bot_username(bot)
    dp = get_dispatcher()
    app.state.telegram_bot = bot
    app.state.telegram_dispatcher = dp
    app.state.telegram_bot_username = bot_username()

    if settings.telegram_mode != "polling":
        url = _webhook_url(settings)
        if url:
            try:
                await bot.set_webhook(
                    url=url, secret_token=settings.telegram_webhook_secret, drop_pending_updates=False
                )
                log.info("telegram_webhook_set")
            except Exception as exc:
                log.warning("telegram_set_webhook_failed", error=type(exc).__name__)
        else:
            log.warning("telegram_webhook_url_missing")
        return

    if _polling_task is not None and not _polling_task.done():
        return
    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception as exc:  # polling still works if a webhook was never set
        log.warning("telegram_delete_webhook_failed", error=type(exc).__name__)

    async def _run() -> None:
        try:
            await dp.start_polling(bot, handle_signals=False, close_bot_session=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("telegram_polling_crashed", error=type(exc).__name__)

    _polling_task = asyncio.get_running_loop().create_task(_run(), name="telegram-polling")
    app.state.telegram_polling_task = _polling_task
    log.info("telegram_polling_started")


async def stop_polling_background(app: FastAPI) -> None:
    """Stop polling (if running) and close the bot HTTP session."""
    global _polling_task, _bot
    dp = _dispatcher
    task = _polling_task
    if task is not None and not task.done():
        if dp is not None:
            with contextlib.suppress(Exception):
                await dp.stop_polling()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    _polling_task = None
    if _bot is not None:
        with contextlib.suppress(Exception):
            await _bot.session.close()
        _bot = None
    for attr in ("telegram_polling_task", "telegram_bot", "telegram_dispatcher"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)
    log.info("telegram_polling_stopped")
