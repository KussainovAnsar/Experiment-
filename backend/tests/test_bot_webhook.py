"""``POST /api/telegram/webhook/{secret}``: secret verification and dispatching to the aiogram Dispatcher."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.bot import telegram as bot
from app.config import get_settings
from tests.test_notifications_support import BOT_USERNAME, FAKE_BOT_TOKEN

UPDATE = {
    "update_id": 1001,
    "message": {
        "message_id": 1,
        "date": 1760000000,
        "chat": {"id": 777001, "type": "private"},
        "from": {"id": 777001, "is_bot": False, "first_name": "Гульнара"},
        "text": "/help",
        "entities": [{"type": "bot_command", "offset": 0, "length": 5}],
    },
}


class FakeDispatcher:
    def __init__(self) -> None:
        self.updates: list[Any] = []

    async def feed_update(self, bot_: Any, update: Any) -> None:
        self.updates.append(update)


@pytest_asyncio.fixture
async def api() -> AsyncIterator[AsyncClient]:
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


@pytest.fixture
def webhook_env(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FakeDispatcher]:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_BOT_TOKEN)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", BOT_USERNAME)  # avoids a getMe network call
    monkeypatch.setenv("TELEGRAM_MODE", "webhook")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret-path")
    get_settings.cache_clear()
    fake = FakeDispatcher()
    monkeypatch.setattr(bot, "get_dispatcher", lambda: fake)
    yield fake
    get_settings.cache_clear()


async def test_webhook_rejects_wrong_or_empty_secret(api: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_BOT_TOKEN)
    get_settings.cache_clear()
    # empty secret setting: always 403, even for an "empty" path segment lookalike
    assert (await api.post("/api/telegram/webhook/anything", json=UPDATE)).status_code == 403
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret-path")
    get_settings.cache_clear()
    assert (await api.post("/api/telegram/webhook/wrong", json=UPDATE)).status_code == 403
    bad_header = await api.post(
        "/api/telegram/webhook/s3cret-path", json=UPDATE, headers={"X-Telegram-Bot-Api-Secret-Token": "nope"}
    )
    assert bad_header.status_code == 403
    get_settings.cache_clear()


async def test_webhook_503_when_bot_disabled(api: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret-path")
    get_settings.cache_clear()
    resp = await api.post("/api/telegram/webhook/s3cret-path", json=UPDATE)
    assert resp.status_code == 503
    get_settings.cache_clear()


async def test_webhook_feeds_update(api: AsyncClient, webhook_env: FakeDispatcher) -> None:
    resp = await api.post(
        "/api/telegram/webhook/s3cret-path", json=UPDATE, headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret-path"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}
    assert len(webhook_env.updates) == 1
    update = webhook_env.updates[0]
    assert update.update_id == 1001 and update.message.text == "/help"

    bad = await api.post("/api/telegram/webhook/s3cret-path", json={"not": "an update"})
    assert bad.status_code == 400
    await bot.stop_polling_background(type("S", (), {"state": type("St", (), {})()})())
