"""Free-tier LLM "revolver" (``LLM_PROVIDER=revolver``): rotates over OpenAI-compatible free endpoints.

Only free tiers are listed (owner's rule: no paid fallbacks). Each endpoint is enabled only when its key is
set; the order and models come from ``REVOLVER_LLM_ORDER`` (``provider:model[@max_images]``, comma separated)
or :data:`DEFAULT_ORDER`. For every lesson the endpoints are tried in order:

* an endpoint on cooldown is skipped;
* frames are thinned evenly to the endpoint's image cap and downscaled to ``REVOLVER_IMAGE_MAX_PX``;
  ``@0`` (or a known text-only model) gets the transcript only, and the saved analysis says so;
* 503 «overloaded» → the same endpoint is retried once after ``OVERLOAD_RETRY_DELAY_SEC``, then a short cooldown
  (``OVERLOAD_COOLDOWN_SEC``); 404 «model not found / no longer available» → cooled for 24 h;
* 429 / other 5xx / timeout / connection error / auth error / unparseable or non-validating JSON → cooldown
  (``Retry-After`` when the server sent it, else ``REVOLVER_COOLDOWN_SEC``, default 10 min) and the next one;
  400 / 413 / 422 (this request does not fit the endpoint, e.g. too many tokens) → next one without cooldown;
* when nothing answered → :class:`FreeLimitsExhausted` («бесплатные лимиты исчерпаны, повторите позже»); the
  worker re-queues the lesson for when the first endpoint cools down. There is never a paid fallback.

The actual request / schema / validation logic is :class:`~app.providers.llm.openai.OpenAILLMProvider`
pointed at another ``base_url``. Free tiers of several providers may use the data for training — see
``docs/PROVIDERS.md`` before sending real lessons of children.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.logging import get_logger
from app.providers.llm.base import (
    FreeLimitsExhausted,
    ImageInput,
    LLMHTTPError,
    LLMResult,
    ProviderNotConfigured,
    available_images,
)
from app.providers.revolver import (
    LLM_COOLDOWNS,
    OVERLOAD_STATUSES,
    CooldownBook,
    OrderEntry,
    cooldown_for,
    exhausted_message,
    parse_order,
)

log = get_logger("app.providers.llm.revolver")

OVERLOAD_RETRY_DELAY_SEC = 5.0  # pause before the single retry of an overloaded (503) endpoint


@dataclass(frozen=True, slots=True)
class Endpoint:
    """An OpenAI-compatible free API: where it lives, which key enables it and which dialect it speaks."""

    base_url: str
    key_field: str  # Settings attribute holding the API key
    label: str
    signup_url: str
    structured: str = "strict"  # strict json_schema with json_object fallback | json_object
    max_tokens_param: str = "max_tokens"
    default_max_images: int = 4
    max_tokens_cap: int = 16000


ENDPOINTS: dict[str, Endpoint] = {
    "gemini": Endpoint(
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "gemini_api_key",
        "Gemini",
        "https://aistudio.google.com/apikey",
        default_max_images=16,
        max_tokens_cap=32000,
    ),
    "openrouter": Endpoint(
        "https://openrouter.ai/api/v1",
        "openrouter_api_key",
        "OpenRouter",
        "https://openrouter.ai/settings/keys",
        structured="json_object",
        default_max_images=8,
    ),
    "mistral": Endpoint(
        "https://api.mistral.ai/v1",
        "mistral_api_key",
        "Mistral",
        "https://console.mistral.ai/api-keys",
        default_max_images=8,
    ),
    "groq": Endpoint(
        "https://api.groq.com/openai/v1",
        "groq_api_key",
        "Groq",
        "https://console.groq.com/keys",
        max_tokens_param="max_completion_tokens",
        default_max_images=3,
        max_tokens_cap=8192,
    ),
    "cerebras": Endpoint(
        "https://api.cerebras.ai/v1",
        "cerebras_api_key",
        "Cerebras",
        "https://cloud.cerebras.ai/",
        max_tokens_param="max_completion_tokens",
        default_max_images=5,
    ),
    "nvidia": Endpoint(
        "https://integrate.api.nvidia.com/v1",
        "nvidia_api_key",
        "NVIDIA NIM",
        "https://build.nvidia.com/",
        structured="json_object",
        default_max_images=4,
        max_tokens_cap=8192,
    ),
    "sambanova": Endpoint(
        "https://api.sambanova.ai/v1",
        "sambanova_api_key",
        "SambaNova",
        "https://cloud.sambanova.ai/apis",
        structured="json_object",
        default_max_images=0,
        max_tokens_cap=8192,
    ),
}

# Vision-capable free models first (best quality / largest quota first), text-only ones last: they are used
# only when every vision endpoint is cooling down, and then the analysis is made from the transcript alone.
DEFAULT_ORDER = ",".join(
    [
        "gemini:gemini-3.8-flash@16",
        "gemini:gemini-3.6-flash@16",
        "gemini:gemini-3.5-flash@16",
        "gemini:gemini-3.1-flash-lite@16",
        "gemini:gemini-3.5-flash-lite@16",
        "openrouter:google/gemma-4-31b-it:free@8",
        "openrouter:nvidia/nemotron-3.5-super-vl:free@8",
        "mistral:mistral-medium-latest@8",
        "nvidia:nvidia/nemotron-3.5-super-vl-120b-a12b@4",
        "cerebras:qwen-3.8-27b@5",
        "groq:qwen/qwen3.8-27b@3",
        "groq:openai/gpt-oss-120b@0",
        "cerebras:gpt-oss-120b@0",
    ]
)
# Model families known to have no image input: treated as @0 even if the order line forgets it.
TEXT_ONLY_MARKERS = ("gpt-oss", "llama-3.3", "deepseek", "allam", "minimax", "qwen3-coder")

NO_FRAMES_PROMPT_NOTE = (
    "\n\nВНИМАНИЕ: изображения кадров этой модели не переданы (бесплатная модель без зрения). "
    "Анализируй только транскрипт: visual_observations оставь пустым, frame_ids не заполняй."
)
NO_FRAMES_ANALYSIS_NOTE = "Кадры урока не учитывались: анализ сделан бесплатной моделью без зрения по транскрипту."


def thin_images(images: list[ImageInput], limit: int) -> list[ImageInput]:
    """Keep ≤ ``limit`` images spread evenly over the lesson (first and last kept)."""
    if limit <= 0:
        return []
    if len(images) <= limit:
        return list(images)
    if limit == 1:
        return [images[0]]
    step = (len(images) - 1) / (limit - 1)
    return [images[i] for i in sorted({round(i * step) for i in range(limit)})]


@dataclass(slots=True)
class _Slot:
    entry: OrderEntry
    endpoint: Endpoint
    api_key: str
    max_images: int


class RevolverLLMProvider:
    """Tries the configured free endpoints in order (see module docstring)."""

    name = "revolver"

    def __init__(
        self,
        settings: Settings,
        *,
        cooldowns: CooldownBook | None = None,
        provider_factory: Any = None,
    ) -> None:
        self.cooldowns = cooldowns or LLM_COOLDOWNS
        self.cooldown_sec = float(settings.revolver_cooldown_sec or 600.0)
        self.timeout = float(settings.revolver_llm_timeout_sec or 300.0)
        self.image_max_px = int(settings.revolver_image_max_px or 0) or None
        try:
            self.max_tokens = int(os.environ.get("LLM_MAX_TOKENS", "16000"))
        except ValueError:
            self.max_tokens = 16000
        self.overload_retry_delay = OVERLOAD_RETRY_DELAY_SEC
        self._factory = provider_factory or self._make_provider
        self._providers: dict[str, Any] = {}
        self.slots: list[_Slot] = []
        missing: set[str] = set()
        for entry in parse_order(settings.revolver_llm_order or DEFAULT_ORDER):
            endpoint = ENDPOINTS.get(entry.provider)
            if endpoint is None:
                log.warning("llm_revolver_unknown_endpoint", endpoint=entry.provider)
                continue
            if not entry.model:
                log.warning("llm_revolver_entry_without_model", endpoint=entry.provider)
                continue
            if entry.provider == "openrouter" and not entry.model.endswith(":free"):
                log.warning("llm_revolver_paid_model_skipped", endpoint=entry.key)  # free-only rule
                continue
            api_key = str(getattr(settings, endpoint.key_field, "") or "").strip()
            if not api_key:
                missing.add(endpoint.key_field.upper())
                continue
            max_images = entry.max_images if entry.max_images is not None else endpoint.default_max_images
            if any(m in entry.model.lower() for m in TEXT_ONLY_MARKERS):
                max_images = 0
            self.slots.append(_Slot(entry, endpoint, api_key, max_images))
        if not self.slots:
            raise ProviderNotConfigured(
                "LLM_PROVIDER=revolver: no free endpoint has a key; set at least one of "
                + ", ".join(sorted(missing or {e.key_field.upper() for e in ENDPOINTS.values()}))
            )
        log.info("llm_revolver_ready", endpoints=[s.entry.key for s in self.slots])

    def _make_provider(self, slot: _Slot) -> Any:
        from app.providers.llm.openai import OpenAILLMProvider

        ep = slot.endpoint
        return OpenAILLMProvider(
            api_key=slot.api_key,
            model=slot.entry.model,
            base_url=ep.base_url,
            name=slot.entry.provider,
            label=f"{ep.label} {slot.entry.model}",
            timeout=self.timeout,
            max_retries=0,  # the revolver itself is the retry: a 429 must move on, not wait
            structured=ep.structured,
            max_tokens_param=ep.max_tokens_param,
            image_detail=None,
            image_max_px=self.image_max_px,
            max_tokens=min(self.max_tokens, ep.max_tokens_cap),
        )

    def _provider(self, slot: _Slot) -> Any:
        key = slot.entry.key
        if key not in self._providers:
            self._providers[key] = self._factory(slot)
        return self._providers[key]

    async def analyze(self, *, system: str, user_text: str, images: list[ImageInput], json_schema: dict) -> LLMResult:
        images = available_images(images)
        failures: list[str] = []
        t0 = time.monotonic()
        for slot in self.slots:
            key = slot.entry.key
            left = self.cooldowns.remaining(key)
            if left > 0:
                failures.append(f"{key}: остывает ещё {round(left)} с")
                continue
            sent = thin_images(images, slot.max_images)
            text = user_text
            if images and not sent:
                text += NO_FRAMES_PROMPT_NOTE
            elif len(sent) < len(images):
                text += (
                    f"\n\nПримечание: из-за лимита бесплатной модели передано {len(sent)} из {len(images)} кадров; "
                    "ссылайся только на переданные кадры."
                )
            try:
                result = await self._call(slot, system=system, user_text=text, images=sent, json_schema=json_schema)
            except Exception as exc:  # noqa: BLE001 - any failure of one free endpoint means "try the next one"
                status = exc.status_code if isinstance(exc, LLMHTTPError) else None
                retry_after = exc.retry_after if isinstance(exc, LLMHTTPError) else None
                cooldown = cooldown_for(status, retry_after, self.cooldown_sec)
                if cooldown is not None:
                    self.cooldowns.cool(key, cooldown)
                log.warning(
                    "llm_revolver_endpoint_failed",
                    endpoint=key,
                    status=status,
                    cooldown_sec=cooldown,
                    error=f"{type(exc).__name__}: {str(exc)[:300]}",
                )
                failures.append(f"{key}: {f'HTTP {status}' if status else type(exc).__name__}")
                continue
            return self._annotate(result, slot, images=len(images), sent=len(sent), t0=t0, skipped=failures)
        retry_after = self._next_available()
        log.error("llm_revolver_exhausted", failures=failures, retry_after_sec=retry_after)
        raise FreeLimitsExhausted(exhausted_message("ИИ-анализа", failures, retry_after), retry_after_sec=retry_after)

    async def _call(self, slot: _Slot, **kwargs: Any) -> LLMResult:
        """One request; an overloaded (503) endpoint gets exactly one more try before the revolver moves on."""
        try:
            return await self._provider(slot).analyze(**kwargs)
        except LLMHTTPError as exc:
            if exc.status_code not in OVERLOAD_STATUSES:
                raise
            log.info("llm_revolver_overloaded_retry", endpoint=slot.entry.key, delay_sec=self.overload_retry_delay)
        await asyncio.sleep(self.overload_retry_delay)
        return await self._provider(slot).analyze(**kwargs)

    def _next_available(self) -> float:
        waits = [self.cooldowns.remaining(s.entry.key) for s in self.slots]
        waits = [w for w in waits if w > 0]
        return min(waits) if waits else self.cooldown_sec

    def _annotate(
        self, result: LLMResult, slot: _Slot, *, images: int, sent: int, t0: float, skipped: list[str]
    ) -> LLMResult:
        key = slot.entry.key
        frames_ignored = images > 0 and sent == 0
        if frames_ignored:
            indicators = result.data.get("manager_indicators")
            if isinstance(indicators, dict):
                notes = str(indicators.get("lesson_structure_notes") or "").strip()
                indicators["lesson_structure_notes"] = f"{NO_FRAMES_ANALYSIS_NOTE} {notes}".strip()
        model = f"{key} [без кадров]" if frames_ignored else key
        result.model = model[:128]
        result.latency_ms = int((time.monotonic() - t0) * 1000)
        result.extra = {
            **(result.extra or {}),
            "revolver": {"endpoint": key, "frames_total": images, "frames_sent": sent, "skipped": skipped},
        }
        log.info(
            "llm_revolver_answered",
            endpoint=key,
            frames_total=images,
            frames_sent=sent,
            skipped=len(skipped),
            latency_ms=result.latency_ms,
        )
        return result
