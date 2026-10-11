"""Messaging providers without a database: HTML conversion, Telegram/WhatsApp adapters over a mock HTTP
transport, and the registry."""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings, get_settings
from app.db.models import Channel
from app.providers.messaging import mock
from app.providers.messaging.base import InlineButton, ProviderNotConfigured, html_to_plain
from app.providers.messaging.mock import MockMessenger
from app.providers.messaging.registry import get_messenger
from app.providers.messaging.telegram import TelegramMessenger
from app.providers.messaging.whatsapp import WhatsAppMessenger, normalize_phone

HTML = (
    "<b>Айдана — русский язык, 11 октября</b>\n<i>Тема:</i> падежи &amp; окончания\n\n"
    "<b>Что повторить дома</b>\n• вопросы падежей\n\n"
    '<a href="http://testserver/p/tok">Полный отчёт — в кабинете родителя</a>'
)


def test_html_to_plain_whatsapp_formatting() -> None:
    text = html_to_plain(HTML)
    assert text.startswith("*Айдана — русский язык, 11 октября*\n_Тема:_ падежи & окончания")
    assert "*Что повторить дома*\n• вопросы падежей" in text
    assert text.endswith("Полный отчёт — в кабинете родителя (http://testserver/p/tok)")
    assert "<" not in text
    assert html_to_plain('<a href="http://x">t</a>', keep_links=False) == "t"
    assert html_to_plain("a<br>b<code>c</code>&lt;d&gt;") == "a\nbc<d>"


def _transport(handler) -> httpx.AsyncClient:  # noqa: ANN001
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_telegram_messenger_payload_and_success() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    async with _transport(handler) as client:
        messenger = TelegramMessenger("123:abc", client=client)
        result = await messenger.send_html(
            "777", HTML, button=InlineButton("Открыть кабинет", "http://testserver/p/tok")
        )
    assert result.ok and result.provider_message_id == "42"
    assert seen[0].url.path == "/bot123:abc/sendMessage"
    body = json.loads(seen[0].content)
    assert body["chat_id"] == 777 and body["parse_mode"] == "HTML" and body["text"] == HTML
    assert body["link_preview_options"] == {"is_disabled": True}
    assert body["reply_markup"] == {
        "inline_keyboard": [[{"text": "Открыть кабинет", "url": "http://testserver/p/tok"}]]
    }


async def test_telegram_messenger_errors_do_not_raise() -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"ok": False, "description": "Forbidden: bot was blocked by the user"})

    def network(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async with _transport(forbidden) as client:
        result = await TelegramMessenger("123:abc", client=client).send_text("777", "hi")
    assert not result.ok and "403" in result.error and "blocked" in result.error
    async with _transport(network) as client:
        result = await TelegramMessenger("123:abc", client=client).send_text("777", "hi")
    assert not result.ok and "ConnectError" in result.error
    with pytest.raises(ProviderNotConfigured):
        TelegramMessenger("")


async def test_whatsapp_messenger_payload_and_disabled() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.ABC"}]})

    async with _transport(handler) as client:
        messenger = WhatsAppMessenger(enabled=True, phone_number_id="555", access_token="tok", client=client)
        result = await messenger.send_html(
            "+7 701 000-00-01", HTML, button=InlineButton("Открыть кабинет", "http://testserver/p/other")
        )
    assert result.ok and result.provider_message_id == "wamid.ABC"
    req = seen[0]
    assert req.url.path == "/v21.0/555/messages"
    assert req.headers["authorization"] == "Bearer tok"
    body = json.loads(req.content)
    assert body["messaging_product"] == "whatsapp" and body["to"] == "77010000001" and body["type"] == "text"
    assert body["text"]["body"].startswith("*Айдана — русский язык, 11 октября*")
    assert body["text"]["body"].endswith("Открыть кабинет: http://testserver/p/other")
    assert body["text"]["preview_url"] is False

    with pytest.raises(ProviderNotConfigured, match="WHATSAPP_ENABLED"):
        WhatsAppMessenger(enabled=False, phone_number_id="", access_token="")
    with pytest.raises(ProviderNotConfigured, match="WHATSAPP_PHONE_NUMBER_ID"):
        WhatsAppMessenger(enabled=True, phone_number_id="", access_token="")


async def test_whatsapp_api_error_and_bad_phone() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "Re-engagement message", "code": 131047}})

    async with _transport(handler) as client:
        messenger = WhatsAppMessenger(enabled=True, phone_number_id="555", access_token="tok", client=client)
        result = await messenger.send_text("+77010000001", "hi")
        assert not result.ok and "131047" not in result.error and "шаблон" in result.error
        bad = await messenger.send_text("12", "hi")
        assert not bad.ok and "номер" in bad.error


def test_normalize_phone() -> None:
    assert normalize_phone("+7 (701) 000-00-01") == "77010000001"
    assert normalize_phone("8 701 000 00 01") == "77010000001"
    assert normalize_phone("123") is None and normalize_phone(None) is None


async def test_registry_mock_override_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(telegram_bot_token="", whatsapp_enabled=False)
    monkeypatch.delenv("MESSAGING_PROVIDER", raising=False)
    with pytest.raises(ProviderNotConfigured, match="TELEGRAM_BOT_TOKEN"):
        get_messenger(settings, Channel.telegram)
    with pytest.raises(ProviderNotConfigured, match="WHATSAPP_ENABLED"):
        get_messenger(settings, "whatsapp")
    with pytest.raises(ProviderNotConfigured):
        get_messenger(settings, Channel.none)
    with pytest.raises(ProviderNotConfigured):
        get_messenger(settings, "sms")
    assert isinstance(get_messenger(Settings(telegram_bot_token="1:a"), Channel.telegram), TelegramMessenger)

    monkeypatch.setenv("MESSAGING_PROVIDER", "mock")
    messenger = get_messenger(settings, Channel.telegram)
    assert isinstance(messenger, MockMessenger)
    mock.reset()
    result = await messenger.send_html("1", "<b>x</b>")
    assert result.ok and mock.sent[0].text == "<b>x</b>" and mock.sent[0].channel == "telegram"
    mock.fail_with = "down"
    assert (await messenger.send_text("1", "y")).error == "down"
    mock.reset()
    assert mock.sent == [] and mock.fail_with is None
    get_settings.cache_clear()
