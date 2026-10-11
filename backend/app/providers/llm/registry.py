"""Select the LLM provider by ``settings.llm_provider``."""

from __future__ import annotations

from collections.abc import Callable

from app.config import Settings
from app.providers.llm.base import LLMProvider, ProviderNotConfigured


def _mock(settings: Settings) -> LLMProvider:
    from app.providers.llm.mock import MockLLMProvider

    return MockLLMProvider()


def _anthropic(settings: Settings) -> LLMProvider:
    from app.providers.llm.anthropic import AnthropicLLMProvider

    return AnthropicLLMProvider(api_key=settings.anthropic_api_key, model=settings.llm_model)


def _openai(settings: Settings) -> LLMProvider:
    from app.providers.llm.openai import OpenAILLMProvider, resolve_model

    return OpenAILLMProvider(api_key=settings.openai_api_key, model=resolve_model(settings.llm_model))


PROVIDERS: dict[str, Callable[[Settings], LLMProvider]] = {
    "mock": _mock,
    "anthropic": _anthropic,
    "openai": _openai,
}


def get_llm_provider(settings: Settings) -> LLMProvider:
    """Instantiate the provider named by ``settings.llm_provider``.

    Raises :class:`ProviderNotConfigured` for unknown names or missing credentials.
    """
    name = (settings.llm_provider or "mock").strip().lower()
    factory = PROVIDERS.get(name)
    if factory is None:
        raise ProviderNotConfigured(f"Unknown LLM_PROVIDER={name!r}; expected one of: {', '.join(sorted(PROVIDERS))}")
    return factory(settings)
