"""Prompt assets for the multimodal lesson analysis (module B2).

:func:`build_system_prompt` combines the subject-agnostic ``system_prompt.md`` with the addendum for the lesson
subject (``subjects/<code>.md``); ``SYSTEM_PROMPT`` is the prompt for the default subject.
:func:`build_user_prompt` assembles the user turn from the transcript, the frame index, the student context
and the prior skill states.
``PROMPT_VERSION`` is written to ``analyses.prompt_version``.
"""

from app.pipeline.prompts.system_prompt import BASE_SYSTEM_PROMPT, SYSTEM_PROMPT, build_system_prompt, subject_addendum
from app.pipeline.prompts.user_prompt import (
    PROMPT_VERSION,
    PromptSkillState,
    build_user_prompt,
    format_skill_vocabulary,
    format_timestamp,
    format_transcript,
    subject_title,
)

__all__ = [
    "BASE_SYSTEM_PROMPT",
    "PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "PromptSkillState",
    "build_system_prompt",
    "build_user_prompt",
    "format_skill_vocabulary",
    "format_timestamp",
    "format_transcript",
    "subject_addendum",
    "subject_title",
]
