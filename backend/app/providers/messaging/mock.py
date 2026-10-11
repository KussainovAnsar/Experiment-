"""In-memory messenger for tests and demos.

Every delivered message is appended to the module-level list :data:`sent`, so tests can assert on what would
have reached the parent. Set :data:`fail_with` to a non-empty string to make the next sends fail with that
error (simulates a provider outage); call :func:`reset` between tests.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from app.providers.messaging.base import InlineButton, SendResult

_ids = itertools.count(1)

# Messages "delivered" by the mock, oldest first.
sent: list[SentMessage] = []
# When set, every send fails with this error until reset.
fail_with: str | None = None


@dataclass(slots=True)
class SentMessage:
    """One message recorded by :class:`MockMessenger`."""

    channel: str
    chat_ref: str
    text: str
    is_html: bool
    button: InlineButton | None
    provider_message_id: str


class MockMessenger:
    """Records messages instead of sending them. ``channel`` is only a label (``telegram`` / ``whatsapp``)."""

    name = "mock"

    def __init__(self, channel: str = "telegram") -> None:
        self.channel = channel

    async def _record(self, chat_ref: str, text: str, is_html: bool, button: InlineButton | None) -> SendResult:
        if fail_with:
            return SendResult.failure(fail_with)
        message_id = f"mock-{next(_ids)}"
        sent.append(
            SentMessage(
                channel=self.channel,
                chat_ref=str(chat_ref),
                text=text,
                is_html=is_html,
                button=button,
                provider_message_id=message_id,
            )
        )
        return SendResult.success(message_id)

    async def send_html(self, chat_ref: str, html: str, *, button: InlineButton | None = None) -> SendResult:
        """Record an HTML message."""
        return await self._record(chat_ref, html, True, button)

    async def send_text(self, chat_ref: str, text: str) -> SendResult:
        """Record a plain-text message."""
        return await self._record(chat_ref, text, False, None)


def reset() -> None:
    """Forget recorded messages and clear the failure flag."""
    global fail_with
    sent.clear()
    fail_with = None
