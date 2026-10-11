"""System prompt loaded from ``system_prompt.md`` next to this module."""

from __future__ import annotations

from pathlib import Path

_PATH = Path(__file__).with_name("system_prompt.md")

SYSTEM_PROMPT: str = _PATH.read_text(encoding="utf-8").strip()
