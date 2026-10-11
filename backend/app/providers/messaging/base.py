"""Messenger protocol, result types and the shared HTML → plain-text converter.

Every messenger implements :class:`Messenger`: it receives a channel-specific chat reference (Telegram chat id
or a WhatsApp phone number in E.164) and aiogram-compatible HTML (``<b>``, ``<i>``, ``<a href>``, ``<code>``
only, no lists) and returns a :class:`SendResult`. Implementations never raise on delivery problems — they
return ``ok=False`` with a human-readable ``error`` — so the notifications service can record the outcome in
``notifications`` and move on to the next parent. Configuration problems (missing token, channel disabled)
are raised as :class:`ProviderNotConfigured` from the registry, before any send is attempted.
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class MessagingError(RuntimeError):
    """Base class for messaging failures."""


class ProviderNotConfigured(MessagingError):
    """The selected channel lacks credentials or is disabled; the message says how to fix it."""


@dataclass(slots=True, frozen=True)
class InlineButton:
    """A single URL button rendered under the message where the channel supports it (Telegram)."""

    text: str
    url: str


@dataclass(slots=True)
class SendResult:
    """Outcome of one send attempt."""

    ok: bool
    provider_message_id: str | None = None
    error: str | None = None

    @classmethod
    def success(cls, provider_message_id: str | None = None) -> SendResult:
        return cls(ok=True, provider_message_id=provider_message_id)

    @classmethod
    def failure(cls, error: str) -> SendResult:
        return cls(ok=False, error=error[:1000])


@runtime_checkable
class Messenger(Protocol):
    """A channel that can deliver a short HTML or plain-text message to one recipient."""

    name: str

    async def send_html(self, chat_ref: str, html: str, *, button: InlineButton | None = None) -> SendResult:
        """Deliver aiogram-compatible HTML; channels without rich text degrade it via :func:`html_to_plain`."""
        ...

    async def send_text(self, chat_ref: str, text: str) -> SendResult:
        """Deliver plain text (no markup interpretation)."""
        ...


# ---------------------------------------------------------------- HTML → plain text

_TAG_RE = re.compile(r"<(/?)([a-zA-Z0-9]+)([^>]*)>")
_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_WS_LINE_RE = re.compile(r"[ \t]+\n")
_MULTI_NL_RE = re.compile(r"\n{3,}")


def html_to_plain(html: str, *, bold: str = "*", italic: str = "_", keep_links: bool = True) -> str:
    """Convert aiogram-style HTML into WhatsApp-flavoured plain text.

    ``<b>`` → ``*bold*``, ``<i>`` → ``_italic_``, ``<a href="u">t</a>`` → ``t (u)`` (or ``t`` when ``keep_links``
    is False), ``<br>`` → newline, ``<code>`` kept verbatim; every other tag is dropped and HTML entities are
    unescaped. Whitespace inside lines is preserved, trailing spaces and runs of blank lines are collapsed.
    """
    out: list[str] = []
    pos = 0
    pending_href: list[str | None] = []
    for m in _TAG_RE.finditer(html):
        out.append(html[pos : m.start()])
        pos = m.end()
        closing, tag, attrs = m.group(1) == "/", m.group(2).lower(), m.group(3)
        if tag in ("b", "strong"):
            out.append(bold)
        elif tag in ("i", "em"):
            out.append(italic)
        elif tag == "br":
            out.append("\n")
        elif tag == "a":
            if not closing:
                href = _HREF_RE.search(attrs)
                pending_href.append(href.group(1) if href else None)
            else:
                href = pending_href.pop() if pending_href else None
                if keep_links and href:
                    out.append(f" ({html_lib.unescape(href)})")
        # any other tag (u, s, code, pre, span, tg-spoiler…) is dropped, its text kept
    out.append(html[pos:])
    text = html_lib.unescape("".join(out))
    text = _WS_LINE_RE.sub("\n", text)
    text = _MULTI_NL_RE.sub("\n\n", text)
    return text.strip()
