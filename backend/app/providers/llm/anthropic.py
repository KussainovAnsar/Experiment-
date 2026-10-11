"""Anthropic (Claude) multimodal provider.

Uses the Messages API with *structured outputs* (``output_config.format = json_schema``), which guarantees a
schema-conforming JSON text block. Forced ``tool_choice`` is deliberately **not** used: Claude Sonnet 5.5 /
Opus 5.5 reject ``tool_choice: {type: "tool"}`` with HTTP 400. Images are sent as base64 image blocks, each
preceded by a text block «Кадр #<index>, <mm:ss>» so the model can cite ``frame_ids``. If the answer does not
validate against ``LessonAnalysis`` the pydantic errors are sent back once and a corrected JSON is requested.

Environment knobs (not in ``Settings``): ``LLM_EFFORT`` (low|medium|high|xhigh|max, default ``high``),
``LLM_MAX_TOKENS`` (default 16000), ``LLM_TIMEOUT_SEC`` (default 600).
"""

from __future__ import annotations

import os
import time
from typing import Any

from app.logging import get_logger
from app.providers.llm.base import (
    ImageInput,
    LLMError,
    LLMResult,
    ProviderNotConfigured,
    available_images,
    correction_request,
    encode_image_base64,
    extract_json,
    validation_errors,
)
from app.schemas.analysis import LessonAnalysis

log = get_logger("app.providers.llm.anthropic")

DEFAULT_MODEL = "claude-sonnet-5-5"
_EFFORTS = {"low", "medium", "high", "xhigh", "max"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class AnthropicLLMProvider:
    """Claude adapter built on ``anthropic.AsyncAnthropic``."""

    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        *,
        max_tokens: int | None = None,
        effort: str | None = None,
        timeout_sec: float | None = None,
    ) -> None:
        if not api_key:
            raise ProviderNotConfigured("ANTHROPIC_API_KEY is empty; set it or use LLM_PROVIDER=mock")
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - installed in the project venv
            raise ProviderNotConfigured("pip install anthropic") from exc
        self._anthropic = anthropic
        self.model = model or DEFAULT_MODEL
        self.max_tokens = max_tokens or _env_int("LLM_MAX_TOKENS", 16000)
        effort = (effort or os.environ.get("LLM_EFFORT") or "high").lower()
        self.effort = effort if effort in _EFFORTS else "high"
        timeout = timeout_sec if timeout_sec is not None else float(os.environ.get("LLM_TIMEOUT_SEC", "600"))
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=2)

    # ------------------------------------------------------------ request building

    def _user_content(self, user_text: str, images: list[ImageInput]) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        for img in images:
            media_type, data = encode_image_base64(img.path)
            content.append({"type": "text", "text": img.label})
            content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}})
        if images:
            content.append(
                {
                    "type": "text",
                    "text": "Выше — ключевые кадры записи с подписями. "
                    "Ссылайся на них через frame_ids по номеру кадра. "
                    "Верни результат одним JSON-объектом по схеме.",
                }
            )
        return content

    async def _call(self, *, system: str, messages: list[dict[str, Any]], schema: dict) -> Any:
        a = self._anthropic
        try:
            return await self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=messages,
                output_config={"format": {"type": "json_schema", "schema": schema}, "effort": self.effort},
            )
        except a.RateLimitError as exc:
            raise LLMError(f"Anthropic rate limit: {exc}") from exc
        except a.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except a.APIConnectionError as exc:
            raise LLMError(f"Anthropic connection error: {exc}") from exc

    @staticmethod
    def _text_of(response: Any) -> str:
        parts = [getattr(b, "text", "") for b in response.content if getattr(b, "type", None) == "text"]
        if not parts:
            raise LLMError("Anthropic response contains no text block")
        return "".join(parts)

    @staticmethod
    def _check_stop(response: Any) -> None:
        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details is not None else None
            raise LLMError(f"Anthropic declined the request (refusal, category={category})")
        if stop == "max_tokens":
            raise LLMError("Anthropic response was cut off by max_tokens; raise LLM_MAX_TOKENS")

    # ------------------------------------------------------------ public API

    async def analyze(self, *, system: str, user_text: str, images: list[ImageInput], json_schema: dict) -> LLMResult:
        """Run the multimodal analysis and return the (schema-validated once) JSON."""
        schema = self._anthropic.transform_schema(json_schema or LessonAnalysis.model_json_schema())
        images = available_images(images)
        messages: list[dict[str, Any]] = [{"role": "user", "content": self._user_content(user_text, images)}]
        t0 = time.monotonic()
        tokens_in = 0
        tokens_out = 0
        data: dict | None = None
        errors: str | None = None
        for attempt in range(2):
            response = await self._call(system=system, messages=messages, schema=schema)
            usage = getattr(response, "usage", None)
            tokens_in += int(getattr(usage, "input_tokens", 0) or 0)
            tokens_out += int(getattr(usage, "output_tokens", 0) or 0)
            self._check_stop(response)
            text = self._text_of(response)
            data = extract_json(text)
            errors = validation_errors(data)
            if errors is None:
                break
            log.warning("llm_anthropic_validation_failed", attempt=attempt, errors=errors[:500])
            # Echo the whole assistant turn (thinking blocks included) so the follow-up stays append-only.
            messages.append(
                {"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in response.content]}
            )
            messages.append({"role": "user", "content": correction_request(errors)})
        if data is None or errors is not None:
            raise LLMError(f"Anthropic output failed LessonAnalysis validation twice:\n{errors}")
        latency_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            "llm_anthropic_done", model=self.model, tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=latency_ms
        )
        return LLMResult(data=data, model=self.model, tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=latency_ms)
