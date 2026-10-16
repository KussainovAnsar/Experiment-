"""Multimodal LLM analysis of a lesson: transcript + keyframes → validated ``LessonAnalysis`` → ``analyses`` row.

``analyze_lesson`` expects ``lesson.transcript`` and ``lesson.frames`` to be loaded (the runner uses
``selectinload``); the student and the prior ``skill_states`` are fetched here. The prompt is built from
:mod:`app.pipeline.prompts`, the provider is chosen by ``settings.llm_provider`` and the result is validated
with ``LessonAnalysis`` before it is written. Nothing is committed — the caller owns the transaction.
"""

from __future__ import annotations

import inspect
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import Analysis, Frame, Lesson, SkillState, Student, Transcript
from app.logging import get_logger
from app.pipeline.prompts import (
    PROMPT_VERSION,
    PromptSkillState,
    build_system_prompt,
    build_user_prompt,
    format_timestamp,
)
from app.providers.llm.base import ImageInput, LLMError, LLMProvider, LLMResult
from app.providers.llm.registry import get_llm_provider
from app.schemas.analysis import STARTER_SKILL_KEYS, LessonAnalysis
from app.subjects import normalize_subject

log = get_logger("app.pipeline.analysis")

# Transcripts longer than this (characters) are elided in the middle; ~1 h of speech is ≈ 60–80k chars.
TRANSCRIPT_MAX_CHARS = 180_000


class AnalysisInputError(RuntimeError):
    """The lesson lacks the artefacts needed for analysis (e.g. no transcript)."""


@dataclass(slots=True)
class PromptBundle:
    """Everything handed to the LLM provider."""

    system: str
    user_text: str
    images: list[ImageInput]
    json_schema: dict
    frames_used: list[int] = field(default_factory=list)
    frames_total: int = 0


# ---------------------------------------------------------------- frame selection


def select_frames(frames: list[Frame], max_frames: int) -> list[Frame]:
    """Pick ≤ ``max_frames`` frames for the model.

    Prefers frames flagged ``selected_for_llm`` and whose file exists; if still too many, thins them evenly
    over the timeline (first and last kept). Returned in index order.
    """
    if max_frames <= 0:
        return []
    ordered = sorted(frames, key=lambda f: (f.index, f.timestamp_sec))
    existing = [f for f in ordered if Path(f.path).is_file()]
    preferred = [f for f in existing if f.selected_for_llm]
    pool = preferred or existing
    if len(pool) <= max_frames:
        return pool
    if max_frames == 1:
        return [pool[0]]
    step = (len(pool) - 1) / (max_frames - 1)
    picks = sorted({round(i * step) for i in range(max_frames)})
    return [pool[i] for i in picks]


def frame_label(frame: Frame) -> str:
    """«Кадр #<index>, <mm:ss>» — the text block shown right before each image."""
    return f"Кадр #{frame.index}, {format_timestamp(frame.timestamp_sec)}"


# ---------------------------------------------------------------- prompt assembly


def _prior_skill_states(states: list[SkillState]) -> list[PromptSkillState]:
    out: list[PromptSkillState] = []
    for st in sorted(
        states, key=lambda s: (s.status.value if hasattr(s.status, "value") else str(s.status), s.skill_key)
    ):
        notes = [str(e.get("note") or "") for e in (st.evidence or [])[-3:] if isinstance(e, dict) and e.get("note")]
        out.append(
            PromptSkillState(
                skill_key=st.skill_key,
                skill_name=st.skill_name,
                status=st.status.value if hasattr(st.status, "value") else str(st.status),
                times_seen=st.times_seen,
                times_struggled=st.times_struggled,
                last_notes=notes,
            )
        )
    return out


def _state_lesson_ids(state: SkillState) -> set[str]:
    ids = {str(e.get("lesson_id")) for e in (state.evidence or []) if isinstance(e, dict) and e.get("lesson_id")}
    if state.last_seen_lesson_id is not None:
        ids.add(str(state.last_seen_lesson_id))
    return ids


async def states_for_subject(session: AsyncSession, states: list[SkillState], subject: str | None) -> list[SkillState]:
    """Keep the skill states observed on lessons of ``subject``.

    ``skill_states`` carry no subject column, so the subject is taken from the lessons in the evidence. A state
    whose lessons can no longer be resolved (deleted) is kept: dropping history silently is worse than showing it.
    """
    if not states:
        return states
    per_state = {st.id: _state_lesson_ids(st) for st in states}
    all_ids: set[uuid.UUID] = set()
    for ids in per_state.values():
        for raw in ids:
            try:
                all_ids.add(uuid.UUID(raw))
            except ValueError:
                continue
    if not all_ids:
        return states
    rows = (await session.execute(select(Lesson.id, Lesson.subject).where(Lesson.id.in_(all_ids)))).all()
    subject_by_lesson = {str(lid): normalize_subject(subj) for lid, subj in rows}
    want = normalize_subject(subject)
    kept: list[SkillState] = []
    for st in states:
        subjects = {subject_by_lesson[i] for i in per_state[st.id] if i in subject_by_lesson}
        if not subjects or want in subjects:
            kept.append(st)
    return kept


def build_prompt(
    lesson: Lesson,
    transcript: Transcript | None,
    frames: list[Frame],
    student: Student | None,
    prior_states: list[SkillState],
    settings: Settings,
) -> PromptBundle:
    """Assemble system prompt, user text, images and the JSON schema for one lesson."""
    chosen = select_frames(list(frames or []), settings.llm_max_frames)
    vocabulary = dict(STARTER_SKILL_KEYS.get(normalize_subject(lesson.subject), {}))
    for st in prior_states:  # keep the student's own keys stable even when they are not in the starter list
        vocabulary.setdefault(st.skill_key, st.skill_name)
    native = student.native_language if student is not None else None
    user_text = build_user_prompt(
        subject=lesson.subject,
        scheduled_at=lesson.scheduled_at,
        duration_sec=lesson.duration_sec or (transcript.duration_sec if transcript else None),
        student_grade=student.grade if student is not None else None,
        student_native_language=native.value if hasattr(native, "value") else (str(native) if native else None),
        language_hints=list(lesson.language_hints or []),
        transcript_segments=list(transcript.segments or []) if transcript is not None else [],
        transcript_language=transcript.language if transcript is not None else None,
        frames=[
            (f.index, f.timestamp_sec, f.reason.value if hasattr(f.reason, "value") else str(f.reason)) for f in chosen
        ],
        frames_total=len(frames or []),
        prior_skills=_prior_skill_states(prior_states),
        skill_vocabulary=vocabulary,
        transcript_max_chars=TRANSCRIPT_MAX_CHARS,
    )
    images = [ImageInput(path=Path(f.path), label=frame_label(f)) for f in chosen]
    return PromptBundle(
        system=build_system_prompt(lesson.subject),
        user_text=user_text,
        images=images,
        json_schema=LessonAnalysis.model_json_schema(),
        frames_used=[f.index for f in chosen],
        frames_total=len(frames or []),
    )


# ---------------------------------------------------------------- persistence


async def upsert_analysis(
    session: AsyncSession,
    lesson_id: uuid.UUID,
    analysis: LessonAnalysis,
    *,
    provider: str,
    model: str | None,
    tokens_in: int | None,
    tokens_out: int | None,
    latency_ms: int | None,
    prompt_version: str = PROMPT_VERSION,
) -> Analysis:
    """Insert or update the ``analyses`` row of ``lesson_id`` (flushed, not committed)."""
    row = (await session.execute(select(Analysis).where(Analysis.lesson_id == lesson_id))).scalar_one_or_none()
    if row is None:
        row = Analysis(lesson_id=lesson_id, provider=provider, data=analysis.model_dump(mode="json"))
        session.add(row)
    row.provider = provider
    row.model = model
    row.prompt_version = prompt_version
    row.data = analysis.model_dump(mode="json")
    row.tokens_in = tokens_in
    row.tokens_out = tokens_out
    row.latency_ms = latency_ms
    await session.flush()
    return row


# ---------------------------------------------------------------- public API


async def analyze_lesson(
    session: AsyncSession, lesson: Lesson, *, provider: LLMProvider | None = None, settings: Settings | None = None
) -> Analysis:
    """Analyse ``lesson`` with the configured LLM provider and upsert its ``analyses`` row.

    Requires ``lesson.transcript`` and ``lesson.frames`` to be loaded. Raises :class:`AnalysisInputError`
    when the transcript is missing, :class:`LLMError` on provider failure and ``pydantic.ValidationError``
    when the provider's JSON does not match ``LessonAnalysis``.
    """
    settings = settings or get_settings()
    transcript = lesson.transcript
    if transcript is None:
        raise AnalysisInputError("Нет транскрипта урока: анализ невозможен")
    frames = list(lesson.frames or [])
    student = await session.get(Student, lesson.student_id)
    prior_states = list(
        (await session.execute(select(SkillState).where(SkillState.student_id == lesson.student_id))).scalars().all()
    )
    prior_states = await states_for_subject(session, prior_states, lesson.subject)
    bundle = build_prompt(lesson, transcript, frames, student, prior_states, settings)
    provider = provider or get_llm_provider(settings)

    t0 = time.monotonic()
    result = provider.analyze(
        system=bundle.system, user_text=bundle.user_text, images=bundle.images, json_schema=bundle.json_schema
    )
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, LLMResult):
        raise LLMError(f"provider {provider.name!r} returned {type(result).__name__}, expected LLMResult")
    try:
        validated = LessonAnalysis.model_validate(result.data)
    except ValidationError:
        log.exception("analysis_validation_failed", provider=provider.name, model=result.model)
        raise
    latency = result.latency_ms or int((time.monotonic() - t0) * 1000)
    row = await upsert_analysis(
        session,
        lesson.id,
        validated,
        provider=provider.name,
        model=result.model,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        latency_ms=latency,
    )
    log.info(
        "analysis_saved",
        lesson_id=str(lesson.id),
        provider=provider.name,
        model=result.model,
        frames_used=len(bundle.frames_used),
        frames_total=bundle.frames_total,
        errors=len(validated.errors),
        skills=len(validated.skills),
        needs_review=validated.manager_indicators.needs_methodist_review,
        latency_ms=latency,
    )
    return row
