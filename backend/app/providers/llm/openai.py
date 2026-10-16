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
    LLMHTTPError,
    LLMResult,
    ProviderNotConfigured,
    available_images,
    correction_request,
    encode_image_base64,
    extract_json,
    validation_errors,
)
from app.providers.revolver import best_retry_after
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


def _retry_after(exc: Any) -> float | None:
    response = getattr(exc, "response", None)
    return best_retry_after(getattr(response, "headers", None), str(exc))


class OpenAILLMProvider:
    """OpenAI adapter built on ``openai.AsyncOpenAI``."""

    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        *,
        max_tokens: int | None = None,
        base_url: str | None = None,
        name: str | None = None,
        label: str = "OpenAI",
        timeout: float | None = None,
        max_retries: int = 2,
        structured: str = "strict",
        max_tokens_param: str = "max_completion_tokens",
        image_detail: str | None = "high",
        image_max_px: int | None = None,
    ) -> None:
        """``base_url``/``name``/``label`` turn this into an adapter for any OpenAI-compatible endpoint (the
        free-tier revolver uses it for Gemini, Groq, OpenRouter, Mistral, …). ``structured`` is ``strict``
        (``json_schema`` strict, falling back to ``json_object`` when the schema is rejected) or ``json_object``
        (schema pasted into the system prompt). ``max_tokens_param`` and ``image_detail`` cover dialects that do
        not know ``max_completion_tokens`` / ``detail``; ``image_max_px`` downscales frames before upload."""
        if not api_key:
            raise ProviderNotConfigured("OPENAI_API_KEY is empty; set it or use LLM_PROVIDER=mock")
        try:
            import openai
        except ImportError as exc:  # pragma: no cover
            raise ProviderNotConfigured("pip install openai") from exc
        self._openai = openai
        if name:
            self.name = name
        self.label = label
        self.model = model or DEFAULT_MODEL
        self.structured = structured
        self.max_tokens_param = max_tokens_param
        self.image_detail = image_detail
        self.image_max_px = image_max_px
        try:
            self.max_tokens = max_tokens or int(os.environ.get("LLM_MAX_TOKENS", "16000"))
        except ValueError:
            self.max_tokens = 16000
        if timeout is None:
            timeout = float(os.environ.get("LLM_TIMEOUT_SEC", "600"))
        self._client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries)

    def _user_content(self, user_text: str, images: list[ImageInput]) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        for img in images:
            media_type, data = encode_image_base64(img.path, max_px=self.image_max_px)
            image_url: dict[str, Any] = {"url": f"data:{media_type};base64,{data}"}
            if self.image_detail:
                image_url["detail"] = self.image_detail
            content.append({"type": "text", "text": img.label})
            content.append({"type": "image_url", "image_url": image_url})
        return content

    async def _call(self, messages: list[dict[str, Any]], response_format: dict) -> Any:
        o = self._openai
        label = self.label
        try:
            return await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format=response_format,
                **{self.max_tokens_param: self.max_tokens},
            )
        except o.RateLimitError as exc:
            raise LLMHTTPError(f"{label} rate limit: {exc}", status_code=429, retry_after=_retry_after(exc)) from exc
        except o.APIStatusError as exc:
            raise LLMHTTPError(
                f"{label} API error {exc.status_code}: {exc.message}",
                status_code=exc.status_code,
                retry_after=_retry_after(exc),
            ) from exc
        except o.APIConnectionError as exc:  # includes APITimeoutError
            raise LLMHTTPError(f"{label} connection error: {exc}") from exc

    def _schema_in_prompt(self, messages: list[dict[str, Any]], schema: dict) -> list[dict[str, Any]]:
        relaxed = [*messages]
        relaxed[0] = {
            "role": "system",
            "content": messages[0]["content"]
            + "\n\nСхема JSON (строго соблюдай):\n"
            + json.dumps(schema, ensure_ascii=False),
        }
        return relaxed

    async def _call_with_fallback(self, messages: list[dict[str, Any]], schema: dict) -> Any:
        if self.structured == "json_object":
            return await self._call(self._schema_in_prompt(messages, schema), {"type": "json_object"})
        strict = {
            "type": "json_schema",
            "json_schema": {"name": "lesson_analysis", "schema": openai_strict_schema(schema), "strict": True},
        }
        try:
            return await self._call(messages, strict)
        except LLMHTTPError as exc:
            text = str(exc).lower()
            if exc.status_code not in (400, 422) or not any(w in text for w in ("schema", "response_format", "json")):
                raise
            log.warning("llm_openai_strict_schema_rejected", provider=self.name, error=str(exc)[:300])
            return await self._call(self._schema_in_prompt(messages, schema), {"type": "json_object"})

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
            if not getattr(completion, "choices", None):
                raise LLMError(f"{self.label} returned no choices")
            choice = completion.choices[0]
            if getattr(choice, "finish_reason", None) == "length":
                raise LLMError(f"{self.label} response was cut off by max_completion_tokens; raise LLM_MAX_TOKENS")
            message = choice.message
            if getattr(message, "refusal", None):
                raise LLMError(f"{self.label} declined the request: {message.refusal}")
            text = message.content or ""
            data = extract_json(text)
            errors = validation_errors(data)
            if errors is None:
                break
            log.warning("llm_openai_validation_failed", provider=self.name, attempt=attempt, errors=errors[:500])
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": correction_request(errors)})
        if data is None or errors is not None:
            raise LLMError(f"{self.label} output failed LessonAnalysis validation twice:\n{errors}")
        latency_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            "llm_openai_done",
            provider=self.name,
            model=self.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
        )
        return LLMResult(data=data, model=self.model, tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=latency_ms)
