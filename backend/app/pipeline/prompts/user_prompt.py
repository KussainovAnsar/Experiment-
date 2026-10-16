"""User-turn assembly for the lesson analysis prompt."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from app.schemas.analysis import RUSSIAN_SKILL_KEYS
from app.subjects import normalize_subject, subject_title

PROMPT_VERSION = "1.0"

_SPEAKER_LABELS = {"teacher": "УЧИТЕЛЬ", "student": "УЧЕНИК"}
_UNKNOWN_LABEL = "НЕИЗВЕСТНО"
_STATUS_RU = {"struggling": "трудности", "developing": "в развитии", "mastered": "освоен"}
_LANG_RU = {"kk": "казахский", "ru": "русский", "en": "английский"}
# Subjects where the language of instruction is itself the subject: native vs second language matters.
_LANGUAGE_SUBJECT_LANG = {"russian": "ru", "kazakh": "kk"}
_LANG_NAME_RU = {"ru": "русский", "kk": "казахский"}


@dataclass(slots=True)
class PromptSkillState:
    """Prior skill state of the student, as shown to the model."""

    skill_key: str
    skill_name: str
    status: str
    times_seen: int = 0
    times_struggled: int = 0
    last_notes: list[str] = field(default_factory=list)


def format_timestamp(seconds: float | None) -> str:
    """``125.4`` → ``02:05``; hours are added when ≥ 1 h (``1:02:05``)."""
    total = max(0, round(seconds or 0.0))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def format_transcript(segments: Iterable[Mapping], *, max_chars: int | None = None) -> str:
    """Render transcript segments as ``[mm:ss] УЧИТЕЛЬ: …`` lines.

    Segments with empty text are skipped. When ``max_chars`` is given the middle of the transcript is
    elided (keeping the beginning and the end) and the cut is marked explicitly.
    """
    lines: list[str] = []
    for seg in segments:
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        speaker = _SPEAKER_LABELS.get(str(seg.get("speaker") or "").lower(), _UNKNOWN_LABEL)
        lines.append(f"[{format_timestamp(float(seg.get('start') or 0.0))}] {speaker}: {text}")
    rendered = "\n".join(lines)
    if max_chars is not None and len(rendered) > max_chars:
        head = rendered[: max_chars // 2]
        tail = rendered[-(max_chars // 2) :]
        rendered = head + "\n[… часть транскрипта пропущена из-за объёма …]\n" + tail
    return rendered


def format_skill_vocabulary(vocabulary: Mapping[str, str] | None = None) -> str:
    """Bullet list ``skill_key — название`` for the prompt.

    ``None`` → the Russian starter list (legacy default); an empty mapping → a note that keys are to be created.
    """
    vocab = RUSSIAN_SKILL_KEYS if vocabulary is None else vocabulary
    if not vocab:
        return (
            "Стартового словаря для этого предмета нет и истории навыков по нему нет: "
            "заводи новые skill_key по правилам из системной инструкции."
        )
    return "\n".join(f"- {key} — {name}" for key, name in vocab.items())


def format_prior_skills(states: Sequence[PromptSkillState]) -> str:
    """Render prior skill states (or a note that there is no history)."""
    if not states:
        return "История навыков отсутствует: это первый проанализированный урок ученика. progress_note оставь null."
    lines = []
    for st in states:
        status = _STATUS_RU.get(st.status, st.status)
        line = (
            f"- {st.skill_key} ({st.skill_name}): статус «{status}», "
            f"уроков: {st.times_seen}, с трудностями: {st.times_struggled}"
        )
        if st.last_notes:
            line += "; последние наблюдения: " + " | ".join(n[:120] for n in st.last_notes[:2])
        lines.append(line)
    return "\n".join(lines)


def build_user_prompt(
    *,
    subject: str | None,
    scheduled_at: datetime | None,
    duration_sec: float | None,
    student_grade: int | None,
    student_native_language: str | None,
    language_hints: Sequence[str] | None,
    transcript_segments: Iterable[Mapping],
    transcript_language: str | None,
    frames: Sequence[tuple[int, float, str]],
    frames_total: int,
    prior_skills: Sequence[PromptSkillState],
    skill_vocabulary: Mapping[str, str] | None = None,
    transcript_max_chars: int | None = None,
) -> str:
    """Build the user turn.

    ``frames`` are ``(index, timestamp_sec, reason)`` of the frames attached as images (in the same order);
    ``frames_total`` is how many frames were extracted in total (so the model knows about thinning).
    """
    parts: list[str] = []
    parts.append("# Контекст урока")
    ctx = [f"- Предмет: {subject_title(subject)}"]
    if scheduled_at is not None:
        ctx.append(f"- Дата урока: {scheduled_at.strftime('%d.%m.%Y')}")
    if duration_sec:
        ctx.append(f"- Длительность записи: {format_timestamp(duration_sec)}")
    ctx.append(f"- Класс ученика: {student_grade if student_grade is not None else 'не указан'}")
    native_code = (student_native_language or "kk").lower()
    native = _LANG_RU.get(native_code, student_native_language or "казахский")
    taught_lang = _LANGUAGE_SUBJECT_LANG.get(normalize_subject(subject))
    if taught_lang is None:
        ctx.append(f"- Родной язык ученика: {native}")
    elif taught_lang == native_code:
        ctx.append(f"- Родной язык ученика: {native}; {_LANG_NAME_RU[taught_lang]} — родной язык")
    else:
        ctx.append(f"- Родной язык ученика: {native}; {_LANG_NAME_RU[taught_lang]} — второй язык")
    hints = ", ".join(language_hints or ["ru", "kk"])
    ctx.append(f"- Ожидаемые языки речи: {hints}")
    if transcript_language:
        ctx.append(f"- Язык, определённый при распознавании: {transcript_language}")
    parts.append("\n".join(ctx))

    parts.append("# История навыков ученика (из предыдущих уроков)")
    parts.append(format_prior_skills(prior_skills))

    parts.append("# Словарь ключей навыков (skill_key)")
    parts.append(format_skill_vocabulary(skill_vocabulary))

    parts.append("# Кадры записи")
    if frames:
        frame_lines = [
            f"- Кадр #{idx} — {format_timestamp(ts)} ({'смена сцены' if reason == 'scene' else 'периодический'})"
            for idx, ts, reason in frames
        ]
        note = ""
        if frames_total > len(frames):
            note = f"\nВсего извлечено кадров: {frames_total}; к запросу приложены {len(frames)} (равномерная выборка)."
        parts.append(
            "\n".join(frame_lines) + note + "\nИзображения приложены ниже в том же порядке, каждое с подписью."
        )
    else:
        parts.append(
            "Кадры недоступны (аудиозапись или видео без полезного изображения). "
            "visual_observations оставь пустым и укажи это в limitations."
        )

    parts.append("# Транскрипт урока")
    transcript = format_transcript(transcript_segments, max_chars=transcript_max_chars)
    parts.append(transcript if transcript else "Транскрипт пуст: речь не распознана. Укажи это в limitations.")

    parts.append(
        "# Задание\n"
        "Проанализируй урок по правилам из системной инструкции и верни один JSON-объект по схеме LessonAnalysis "
        f'(schema_version "{PROMPT_VERSION}"). Все таймкоды — в секундах от начала записи.'
    )
    return "\n\n".join(parts)
