"""OpenAI multimodal provider (``chat.completions`` with ``response_format=json_schema, strict=True``).

Images are sent as ``image_url`` data URLs, each preceded by its label. OpenAI strict mode needs every
property listed in ``required`` and ``additionalProperties: false`` everywhere, so the pydantic schema is
rewritten by :func:`openai_strict_schema` (optional fields stay nullable through ``anyOf``). If the API
rejects the schema, the call falls back to ``json_object`` mode with the schema pasted into the prompt.

Environment knobs: ``OPENAI_LLM_MODEL`` (default ``gpt-5``; ``settings.llm_model`` is used only when it is
not a Claude id), ``LLM_MAX_TOKENS`` (default 16000), ``LLM_TIMEOUT_SEC`` (default 600).
"""

from __future__ import annotations

import copy
import json
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

log = get_logger("app.providers.llm.openai")

DEFAULT_MODEL = "gpt-5"
_UNSUPPORTED_KEYS = {
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "default",
    "title",
}


def openai_strict_schema(schema: dict) -> dict:
    """Return a copy of ``schema`` that satisfies OpenAI strict structured-output rules."""

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        out = {k: walk(v) for k, v in node.items() if k not in _UNSUPPORTED_KEYS}
        if out.get("type") == "object" and "properties" in out:
            out["required"] = list(out["properties"].keys())
            out["additionalProperties"] = False
        return out

    return walk(copy.deepcopy(schema))


def resolve_model(settings_model: str) -> str:
    """Pick the OpenAI model: env override, else ``settings.llm_model`` unless it is a Claude id."""
    env = os.environ.get("OPENAI_LLM_MODEL", "").strip()
    if env:
        return env
    if settings_model and not settings_model.lower().startswith("claude"):
        return settings_model
    return DEFAULT_MODEL


class OpenAILLMProvider:
    """OpenAI adapter built on ``openai.AsyncOpenAI``."""

    name = "openai"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, *, max_tokens: int | None = None) -> None:
        if not api_key:
            raise ProviderNotConfigured("OPENAI_API_KEY is empty; set it or use LLM_PROVIDER=mock")
        try:
            import openai
        except ImportError as exc:  # pragma: no cover
            raise ProviderNotConfigured("pip install openai") from exc
        self._openai = openai
        self.model = model or DEFAULT_MODEL
        try:
            self.max_tokens = max_tokens or int(os.environ.get("LLM_MAX_TOKENS", "16000"))
        except ValueError:
            self.max_tokens = 16000
        timeout = float(os.environ.get("LLM_TIMEOUT_SEC", "600"))
        self._client = openai.AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=2)

    def _user_content(self, user_text: str, images: list[ImageInput]) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        for img in images:
            media_type, data = encode_image_base64(img.path)
            content.append({"type": "text", "text": img.label})
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{data}", "detail": "high"}}
            )
        return content

    async def _call(self, messages: list[dict[str, Any]], response_format: dict) -> Any:
        o = self._openai
        try:
            return await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format=response_format,
                max_completion_tokens=self.max_tokens,
            )
        except o.RateLimitError as exc:
            raise LLMError(f"OpenAI rate limit: {exc}") from exc
        except o.APIStatusError as exc:
            raise LLMError(f"OpenAI API error {exc.status_code}: {exc.message}") from exc
        except o.APIConnectionError as exc:
            raise LLMError(f"OpenAI connection error: {exc}") from exc

    async def _call_with_fallback(self, messages: list[dict[str, Any]], schema: dict) -> Any:
        strict = {
            "type": "json_schema",
            "json_schema": {"name": "lesson_analysis", "schema": openai_strict_schema(schema), "strict": True},
        }
        try:
            return await self._call(messages, strict)
        except LLMError as exc:
            if "400" not in str(exc) or "schema" not in str(exc).lower():
                raise
            log.warning("llm_openai_strict_schema_rejected", error=str(exc)[:300])
            relaxed = [*messages]
            relaxed[0] = {
                "role": "system",
                "content": messages[0]["content"]
                + "\n\nСхема JSON (строго соблюдай):\n"
                + json.dumps(schema, ensure_ascii=False),
            }
            return await self._call(relaxed, {"type": "json_object"})

    async def analyze(self, *, system: str, user_text: str, images: list[ImageInput], json_schema: dict) -> LLMResult:
        """Run the multimodal analysis and return the (schema-validated once) JSON."""
        json_schema = json_schema or LessonAnalysis.model_json_schema()
        images = available_images(images)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": self._user_content(user_text, images)},
        ]
        t0 = time.monotonic()
        tokens_in = tokens_out = 0
        data: dict | None = None
        errors: str | None = None
        for attempt in range(2):
            completion = await self._call_with_fallback(messages, json_schema)
            usage = getattr(completion, "usage", None)
            tokens_in += int(getattr(usage, "prompt_tokens", 0) or 0)
            tokens_out += int(getattr(usage, "completion_tokens", 0) or 0)
            choice = completion.choices[0]
            if getattr(choice, "finish_reason", None) == "length":
                raise LLMError("OpenAI response was cut off by max_completion_tokens; raise LLM_MAX_TOKENS")
            message = choice.message
            if getattr(message, "refusal", None):
                raise LLMError(f"OpenAI declined the request: {message.refusal}")
            text = message.content or ""
            data = extract_json(text)
            errors = validation_errors(data)
            if errors is None:
                break
            log.warning("llm_openai_validation_failed", attempt=attempt, errors=errors[:500])
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": correction_request(errors)})
        if data is None or errors is not None:
            raise LLMError(f"OpenAI output failed LessonAnalysis validation twice:\n{errors}")
        latency_ms = int((time.monotonic() - t0) * 1000)
        log.info("llm_openai_done", model=self.model, tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=latency_ms)
        return LLMResult(data=data, model=self.model, tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=latency_ms)
