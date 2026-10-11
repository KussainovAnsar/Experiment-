"""WhatsApp messenger via the Meta Cloud API (``POST /{phone_number_id}/messages``).

This is a working text-message adapter, but WhatsApp has business prerequisites that code alone cannot
satisfy (Meta Business verification, an approved message template for messages outside the 24-hour customer
service window, per-conversation pricing) — see ``docs/MESSAGING.md``. The adapter sends free-form text,
which Meta accepts only inside an open 24-hour window; outside it the API returns an error that we record
as a failed notification. HTML is converted to WhatsApp formatting (``*bold*``, ``_italic_``).
"""

from __future__ import annotations

import re

import httpx

from app.logging import get_logger
from app.providers.messaging.base import InlineButton, ProviderNotConfigured, SendResult, html_to_plain

log = get_logger("app.providers.messaging.whatsapp")

GRAPH_API_BASE = "https://graph.facebook.com"
GRAPH_API_VERSION = "v21.0"
WHATSAPP_TEXT_LIMIT = 4096
_NON_DIGIT_RE = re.compile(r"\D+")


class WhatsAppMessenger:
    """Send text messages through the WhatsApp Business Cloud API."""

    name = "whatsapp"

    def __init__(
        self,
        *,
        enabled: bool,
        phone_number_id: str,
        access_token: str,
        api_base: str = GRAPH_API_BASE,
        api_version: str = GRAPH_API_VERSION,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not enabled:
            raise ProviderNotConfigured(
                "WhatsApp выключен (WHATSAPP_ENABLED=false). Для включения нужны верификация Meta Business, "
                "WHATSAPP_PHONE_NUMBER_ID и WHATSAPP_ACCESS_TOKEN — см. docs/MESSAGING.md"
            )
        if not phone_number_id or not access_token:
            raise ProviderNotConfigured("WhatsApp: не заданы WHATSAPP_PHONE_NUMBER_ID / WHATSAPP_ACCESS_TOKEN")
        self._phone_number_id = phone_number_id
        self._access_token = access_token
        self._api_base = api_base.rstrip("/")
        self._api_version = api_version
        self._timeout = timeout
        self._client = client

    @property
    def _url(self) -> str:
        return f"{self._api_base}/{self._api_version}/{self._phone_number_id}/messages"

    async def _send_body(self, chat_ref: str, body: str) -> SendResult:
        to = normalize_phone(chat_ref)
        if not to:
            return SendResult.failure("WhatsApp: у родителя не указан номер телефона в международном формате")
        if len(body) > WHATSAPP_TEXT_LIMIT:
            return SendResult.failure(f"WhatsApp: сообщение длиннее {WHATSAPP_TEXT_LIMIT} символов")
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": body},
        }
        headers = {"Authorization": f"Bearer {self._access_token}"}
        try:
            if self._client is not None:
                response = await self._client.post(self._url, json=payload, headers=headers, timeout=self._timeout)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(self._url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            log.warning("whatsapp_send_network_error", error=type(exc).__name__)
            return SendResult.failure(f"WhatsApp: сетевая ошибка ({type(exc).__name__})")
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code < 300 and data.get("messages"):
            return SendResult.success(str(data["messages"][0].get("id") or "") or None)
        err = data.get("error") or {}
        description = err.get("message") or response.text[:200] or "unknown error"
        code = err.get("code")
        log.warning("whatsapp_send_failed", status_code=response.status_code, code=code)
        hint = ""
        if code in (131047, 131026, 131051, 130429):
            hint = " (вне 24-часового окна нужен одобренный шаблон сообщения)"
        return SendResult.failure(f"WhatsApp API {response.status_code}: {description}{hint}")

    async def send_html(self, chat_ref: str, html: str, *, button: InlineButton | None = None) -> SendResult:
        """Convert HTML to WhatsApp formatting and send; a button becomes a trailing ``text: url`` line."""
        body = html_to_plain(html)
        if button is not None and button.url not in body:
            body = f"{body}\n\n{button.text}: {button.url}"
        return await self._send_body(chat_ref, body)

    async def send_text(self, chat_ref: str, text: str) -> SendResult:
        """Send plain text as-is."""
        return await self._send_body(chat_ref, text)


def normalize_phone(raw: str | None) -> str | None:
    """Digits-only E.164 without ``+`` as the Cloud API expects (``+7 701 000-00-00`` → ``77010000000``).

    A Kazakhstan local ``8 7xx ...`` prefix is rewritten to ``7``; anything shorter than 10 digits is rejected.
    """
    if not raw:
        return None
    digits = _NON_DIGIT_RE.sub("", raw)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) < 10 or len(digits) > 15:
        return None
    return digits
