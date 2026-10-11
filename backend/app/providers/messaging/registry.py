"""Select a :class:`~app.providers.messaging.base.Messenger` for a parent's channel.

``MESSAGING_PROVIDER=mock`` (environment variable, not part of ``Settings``) forces the in-memory mock for
every channel — tests and the demo seed use it so nothing leaves the machine. Otherwise the channel maps to
the real adapter, which raises :class:`ProviderNotConfigured` when its credentials are missing or the
channel is disabled.
"""

from __future__ import annotations

import os

from app.config import Settings
from app.db.models import Channel
from app.providers.messaging.base import Messenger, ProviderNotConfigured

MESSAGING_PROVIDER_ENV = "MESSAGING_PROVIDER"


def messaging_provider_override() -> str:
    """Return the lower-cased ``MESSAGING_PROVIDER`` env value (``""`` = use real adapters)."""
    return os.environ.get(MESSAGING_PROVIDER_ENV, "").strip().lower()


def get_messenger(settings: Settings, channel: Channel | str) -> Messenger:
    """Instantiate the messenger for ``channel`` (``telegram`` / ``whatsapp``).

    Raises :class:`ProviderNotConfigured` for ``none``, unknown channels, a disabled WhatsApp, or a missing
    Telegram token; the notifications service turns that into a ``failed`` notification with the message.
    """
    name = channel.value if isinstance(channel, Channel) else str(channel).strip().lower()
    if name == Channel.none.value:
        raise ProviderNotConfigured("Канал уведомлений не выбран (preferred_channel=none)")
    if name not in (Channel.telegram.value, Channel.whatsapp.value):
        raise ProviderNotConfigured(f"Неизвестный канал уведомлений: {name!r}")

    if messaging_provider_override() == "mock":
        from app.providers.messaging.mock import MockMessenger

        return MockMessenger(channel=name)

    if name == Channel.telegram.value:
        from app.providers.messaging.telegram import TelegramMessenger

        return TelegramMessenger(settings.telegram_bot_token)

    from app.providers.messaging.whatsapp import WhatsAppMessenger

    return WhatsAppMessenger(
        enabled=settings.whatsapp_enabled,
        phone_number_id=settings.whatsapp_phone_number_id,
        access_token=settings.whatsapp_access_token,
    )
