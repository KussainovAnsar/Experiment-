"""System prompt = subject-agnostic base (``system_prompt.md``) + per-subject addendum (``subjects/<code>.md``).

Addenda are loaded at import time for every code in :data:`app.subjects.SUBJECTS`; an unknown code gets the
base part only. :func:`build_system_prompt` is what the analysis step sends to the LLM.
"""

from __future__ import annotations

from pathlib import Path

from app.subjects import SUBJECTS, normalize_subject, subject_title

_DIR = Path(__file__).parent
_SUBJECTS_DIR = _DIR / "subjects"

BASE_SYSTEM_PROMPT: str = (_DIR / "system_prompt.md").read_text(encoding="utf-8").strip()

_CLOSING = "Верни только JSON по схеме. Без markdown, без пояснений вне JSON."


def _load_addenda() -> dict[str, str]:
    out: dict[str, str] = {}
    for code in SUBJECTS:
        path = _SUBJECTS_DIR / f"{code}.md"
        if path.is_file():
            out[code] = path.read_text(encoding="utf-8").strip()
    return out


SUBJECT_ADDENDA: dict[str, str] = _load_addenda()


def subject_addendum(subject: str | None) -> str | None:
    """Subject-specific instructions for a lesson subject code, or ``None`` for unknown codes."""
    return SUBJECT_ADDENDA.get(normalize_subject(subject))


def build_system_prompt(subject: str | None) -> str:
    """Full system prompt for a lesson of ``subject`` (empty → default subject; unknown → base part only)."""
    parts = [BASE_SYSTEM_PROMPT]
    addendum = subject_addendum(subject)
    if addendum:
        parts.append(f"## Предмет урока: {subject_title(subject)}\n\n{addendum}")
    parts.append(_CLOSING)
    return "\n\n".join(parts)


# Backward-compatible constant: the full prompt for the default subject.
SYSTEM_PROMPT: str = build_system_prompt(None)
