"""``POST /api/telegram/webhook/{secret}`` — Telegram webhook endpoint (``TELEGRAM_MODE=webhook``).

The path secret must equal ``TELEGRAM_WEBHOOK_SECRET`` (403 otherwise, and always 403 when the setting is
empty). When Telegram sends the ``X-Telegram-Bot-Api-Secret-Token`` header (set by ``setWebhook``) it must
match as well. The update is fed to the aiogram Dispatcher; handler errors never leak to Telegram as 5xx,
otherwise Telegram would retry the same update indefinitely.
"""

from __future__ import annotations

import secrets as secrets_lib
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import ValidationError

from app.config import get_settings
from app.logging import get_logger

log = get_logger("app.api.telegram")

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


def _same(a: str, b: str) -> bool:
    # Compare bytes: ``compare_digest`` on str raises TypeError for non-ASCII input (→ 500 instead of 403).
    return secrets_lib.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _check_secret(path_secret: str, header_secret: str | None) -> None:
    expected = get_settings().telegram_webhook_secret
    if not expected or not _same(path_secret, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступ запрещён")
    if header_secret is not None and not _same(header_secret, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступ запрещён")


@router.post("/webhook/{secret}")
async def telegram_webhook(
    secret: str,
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Receive one Telegram ``Update`` and dispatch it to the bot handlers."""
    _check_secret(secret, x_telegram_bot_api_secret_token)

    from aiogram.types import Update

    from app.bot.telegram import ensure_bot_username, get_bot, get_dispatcher

    bot = get_bot()
    if bot is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Telegram-бот не настроен")
    try:
        payload = await request.json()
        update = Update.model_validate(payload, context={"bot": bot})
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректное обновление Telegram") from exc

    await ensure_bot_username(bot)
    try:
        await get_dispatcher().feed_update(bot, update)
    except Exception:  # log and acknowledge: Telegram must not retry forever
        log.exception("telegram_update_failed", update_id=update.update_id)
    return {"ok": True}
