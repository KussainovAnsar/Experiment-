"""Mock STT provider used in tests and demos.

Returns ``demo/lesson_01/transcript.json`` (module E fixture) when it exists, otherwise a short built-in
bilingual (ru/kk) transcript. The audio content is never inspected. Override the fixture path with
``MOCK_TRANSCRIPT_PATH``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.config import REPO_DIR
from app.logging import get_logger
from app.providers.stt.base import (
    SPEAKER_STUDENT,
    SPEAKER_TEACHER,
    SPEAKER_UNKNOWN,
    Segment,
    TranscriptResult,
    primary_language,
)

log = get_logger("app.providers.stt.mock")

DEFAULT_FIXTURE = REPO_DIR / "demo" / "lesson_01" / "transcript.json"

# (start, end, speaker, text) — a tiny Russian lesson with Kazakh code-switching.
_FALLBACK: list[tuple[float, float, str, str]] = [
    (0.0, 4.5, SPEAKER_TEACHER, "Здравствуй! Сегодня мы повторяем падежные окончания имён существительных."),
    (4.5, 7.0, SPEAKER_STUDENT, "Здравствуйте. Жақсы, түсіндім."),
    (7.0, 13.0, SPEAKER_TEACHER, "Скажи, пожалуйста: «Я иду к ... (школа)». Какое окончание?"),
    (13.0, 16.0, SPEAKER_STUDENT, "Я иду к школа... к школе."),
    (16.0, 22.0, SPEAKER_TEACHER, "Правильно — «к школе», дательный падеж. Мектепке — к школе. Молодец!"),
    (22.0, 27.0, SPEAKER_TEACHER, "Теперь напиши слово «учится» в предложении «Он учится в пятом классе»."),
    (27.0, 31.0, SPEAKER_STUDENT, "Он учиться в пятом классе."),
    (31.0, 38.0, SPEAKER_TEACHER, "Проверим вопросом: «что делает?» — учится, без мягкого знака. Запомни это правило."),
    (38.0, 41.0, SPEAKER_STUDENT, "Поняла. Что делает — учится."),
]


def fixture_path() -> Path:
    """Path of the transcript fixture (env override or the demo default)."""
    return Path(os.environ.get("MOCK_TRANSCRIPT_PATH") or DEFAULT_FIXTURE)


def _normalize_speaker(raw: object, speaker_map: dict) -> str:
    s = str(raw or "").strip()
    if s in (SPEAKER_TEACHER, SPEAKER_STUDENT, SPEAKER_UNKNOWN):
        return s
    mapped = speaker_map.get(s)
    if mapped in (SPEAKER_TEACHER, SPEAKER_STUDENT):
        return str(mapped)
    return SPEAKER_UNKNOWN


def load_fixture(path: Path) -> TranscriptResult:
    """Parse a ``transcript.json`` fixture into a :class:`TranscriptResult`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    speaker_map = dict(data.get("speaker_map") or {})
    segments = [
        Segment(
            start=float(s.get("start", 0.0)),
            end=float(s.get("end", s.get("start", 0.0))),
            speaker=_normalize_speaker(s.get("speaker"), speaker_map),
            text=str(s.get("text", "")),
            confidence=float(s["confidence"]) if s.get("confidence") is not None else None,
        )
        for s in data.get("segments", [])
    ]
    if not speaker_map:
        speaker_map = {"SPEAKER_00": SPEAKER_TEACHER, "SPEAKER_01": SPEAKER_STUDENT}
    return TranscriptResult(
        provider=str(data.get("provider") or "mock"),
        language=data.get("language") or "ru",
        segments=segments,
        speaker_map=speaker_map,
    )


def fallback_transcript(language: str | None = "ru") -> TranscriptResult:
    """Built-in short bilingual transcript (independent of the demo fixtures)."""
    segments = [Segment(start=a, end=b, speaker=sp, text=t, confidence=0.9) for a, b, sp, t in _FALLBACK]
    return TranscriptResult(
        provider="mock",
        language=language or "ru",
        segments=segments,
        speaker_map={"SPEAKER_00": SPEAKER_TEACHER, "SPEAKER_01": SPEAKER_STUDENT},
    )


class MockSTTProvider:
    """Deterministic provider: fixture file if present, else :func:`fallback_transcript`."""

    name = "mock"

    def __init__(self, fixture: Path | None = None) -> None:
        self._fixture = fixture

    async def transcribe(self, audio_path: Path, *, language_hints: list[str]) -> TranscriptResult:
        """Return the fixture transcript; ``audio_path`` is only checked for existence in logs."""
        path = self._fixture or fixture_path()
        if path.is_file():
            try:
                result = load_fixture(path)
                result.provider = "mock"
                log.info("stt_mock_fixture", path=str(path), segments=len(result.segments))
                return result
            except (OSError, ValueError, KeyError) as exc:
                log.warning("stt_mock_fixture_invalid", path=str(path), error=str(exc))
        log.info("stt_mock_fallback", audio_exists=Path(audio_path).exists())
        return fallback_transcript(primary_language(language_hints) or "ru")
