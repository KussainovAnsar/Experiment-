"""Telegram messenger: plain ``httpx`` calls to the Bot API ``sendMessage`` method.

Kept independent from aiogram on purpose: the notifications service (and the worker process) can send parent
reports without a running Dispatcher. HTML parse mode, link previews disabled, optional single inline URL
button. Delivery problems become ``SendResult(ok=False, error=...)``; nothing is raised.
"""

from __future__ import annotations

import httpx

from app.logging import get_logger
from app.providers.messaging.base import InlineButton, MessagingError, ProviderNotConfigured, SendResult

log = get_logger("app.providers.messaging.telegram")

TELEGRAM_API_BASE = "https://api.telegram.org"
# Bot API hard limit for message text; the report renderer stays well below (≤ 1500).
TELEGRAM_TEXT_LIMIT = 4096


class TelegramMessenger:
    """Send messages through the Telegram Bot API with ``httpx``."""

    name = "telegram"

    def __init__(
        self,
        token: str,
        *,
        api_base: str = TELEGRAM_API_BASE,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not token:
            raise ProviderNotConfigured("TELEGRAM_BOT_TOKEN не задан — Telegram-канал выключен")
        self._token = token
        self._api_base = api_base.rstrip("/")
        self._timeout = timeout
        self._client = client

    def _url(self, method: str) -> str:
        return f"{self._api_base}/bot{self._token}/{method}"

    async def _call(self, method: str, payload: dict) -> SendResult:
        try:
            if self._client is not None:
                response = await self._client.post(self._url(method), json=payload, timeout=self._timeout)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(self._url(method), json=payload)
        except httpx.HTTPError as exc:
            log.warning("telegram_send_network_error", error=type(exc).__name__)
            return SendResult.failure(f"Telegram: сетевая ошибка ({type(exc).__name__})")
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code == 200 and body.get("ok"):
            message_id = body.get("result", {}).get("message_id")
            return SendResult.success(str(message_id) if message_id is not None else None)
        description = body.get("description") or response.text[:200] or "unknown error"
        log.warning("telegram_send_failed", status_code=response.status_code, description=description)
        return SendResult.failure(f"Telegram API {response.status_code}: {description}")

    async def send_html(self, chat_ref: str, html: str, *, button: InlineButton | None = None) -> SendResult:
        """Send ``html`` (aiogram-compatible subset) to ``chat_ref`` with previews disabled."""
        if len(html) > TELEGRAM_TEXT_LIMIT:
            return SendResult.failure(f"Telegram: сообщение длиннее {TELEGRAM_TEXT_LIMIT} символов")
        payload: dict = {
            "chat_id": _chat_id(chat_ref),
            "text": html,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if button is not None:
            payload["reply_markup"] = {"inline_keyboard": [[{"text": button.text, "url": button.url}]]}
        return await self._call("sendMessage", payload)

    async def send_text(self, chat_ref: str, text: str) -> SendResult:
        """Send plain text (no parse mode)."""
        if len(text) > TELEGRAM_TEXT_LIMIT:
            return SendResult.failure(f"Telegram: сообщение длиннее {TELEGRAM_TEXT_LIMIT} символов")
        payload = {"chat_id": _chat_id(chat_ref), "text": text, "link_preview_options": {"is_disabled": True}}
        return await self._call("sendMessage", payload)


def _chat_id(chat_ref: str) -> int | str:
    """Telegram accepts numeric chat ids or ``@username``; we store numeric ids as text/int."""
    ref = str(chat_ref).strip()
    if not ref:
        raise MessagingError("empty chat reference")
    try:
        return int(ref)
    except ValueError:
        return ref
