"""Lesson subjects: the single source of truth for subject codes and their Russian titles.

The codes are stored in ``lessons.subject``, ``students.target_subject`` and ``teachers.subjects``; the
analysis prompt picks a subject-specific addendum by code (``app/pipeline/prompts/subjects/<code>.md``).
The frontend reads this list from ``GET /api/subjects``. Unknown codes are kept as-is (free text from older
records) and analysed with the subject-agnostic part of the prompt only.
"""

from __future__ import annotations

DEFAULT_SUBJECT = "russian"

# Order = order in UI selectors.
SUBJECTS: dict[str, str] = {
    "russian": "Русский язык",
    "kazakh": "Казахский язык",
    "english": "Английский язык",
    "math": "Математика",
    "physics": "Физика",
    "chemistry": "Химия",
    "biology": "Биология",
    "history": "История",
    "informatics": "Информатика",
    "ent_prep": "Подготовка к ЕНТ",
    "ielts": "IELTS",
}


def normalize_subject(subject: str | None) -> str:
    """Lower-cased, stripped code; empty → :data:`DEFAULT_SUBJECT`."""
    code = (subject or "").strip().lower()
    return code or DEFAULT_SUBJECT


def is_known_subject(subject: str | None) -> bool:
    return normalize_subject(subject) in SUBJECTS


def subject_title(subject: str | None) -> str:
    """Russian title for a subject code (``russian`` → «Русский язык»); unknown codes are returned as-is."""
    if not subject or not subject.strip():
        return SUBJECTS[DEFAULT_SUBJECT]
    return SUBJECTS.get(normalize_subject(subject), subject.strip())
