"""Prompt assets for the multimodal lesson analysis (module B2).

``SYSTEM_PROMPT`` is loaded from ``system_prompt.md`` at import time; :func:`build_user_prompt` assembles the
user turn from the transcript, the frame index, the student context and the prior skill states.
``PROMPT_VERSION`` is written to ``analyses.prompt_version``.
"""

from app.pipeline.prompts.system_prompt import SYSTEM_PROMPT
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
    "PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "PromptSkillState",
    "build_user_prompt",
    "format_skill_vocabulary",
    "format_timestamp",
    "format_transcript",
    "subject_title",
]
