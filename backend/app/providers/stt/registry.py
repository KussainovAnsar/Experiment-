"""Select the STT provider by ``settings.stt_provider``."""

from __future__ import annotations

from collections.abc import Callable

from app.config import Settings
from app.providers.stt.base import ProviderNotConfigured, STTProvider


def _mock(settings: Settings) -> STTProvider:
    from app.providers.stt.mock import MockSTTProvider

    return MockSTTProvider()


def _whisper_local(settings: Settings) -> STTProvider:
    from app.providers.stt.whisper_local import WhisperLocalProvider

    return WhisperLocalProvider(model_name=settings.whisper_model)


def _openai(settings: Settings) -> STTProvider:
    from app.providers.stt.openai import OpenAISTTProvider

    return OpenAISTTProvider(api_key=settings.openai_api_key, ffmpeg_bin=settings.ffmpeg_bin)


def _deepgram(settings: Settings) -> STTProvider:
    from app.providers.stt.deepgram import DeepgramProvider

    return DeepgramProvider(api_key=settings.deepgram_api_key)


def _assemblyai(settings: Settings) -> STTProvider:
    from app.providers.stt.assemblyai import AssemblyAIProvider

    return AssemblyAIProvider(api_key=settings.assemblyai_api_key)


def _google(settings: Settings) -> STTProvider:
    from app.providers.stt.google import GoogleSTTProvider

    return GoogleSTTProvider(credentials_path=settings.google_application_credentials, ffmpeg_bin=settings.ffmpeg_bin)


def _elevenlabs(settings: Settings) -> STTProvider:
    from app.providers.stt.elevenlabs import ElevenLabsProvider

    return ElevenLabsProvider(api_key=settings.elevenlabs_api_key)


def _yandex(settings: Settings) -> STTProvider:
    from app.providers.stt.yandex import YandexSpeechKitProvider

    return YandexSpeechKitProvider(api_key=settings.yandex_api_key, folder_id=settings.yandex_folder_id)


def _revolver(settings: Settings) -> STTProvider:
    from app.providers.stt.revolver import RevolverSTTProvider

    return RevolverSTTProvider(settings)


PROVIDERS: dict[str, Callable[[Settings], STTProvider]] = {
    "mock": _mock,
    "whisper_local": _whisper_local,
    "openai": _openai,
    "deepgram": _deepgram,
    "assemblyai": _assemblyai,
    "google": _google,
    "elevenlabs": _elevenlabs,
    "yandex": _yandex,
    "revolver": _revolver,
}


def get_stt_provider(settings: Settings) -> STTProvider:
    """Instantiate the provider named by ``settings.stt_provider``.

    Raises :class:`ProviderNotConfigured` for unknown names; credential checks happen inside the provider
    constructor (missing key) or at first ``transcribe`` (missing optional dependency).
    """
    name = (settings.stt_provider or "mock").strip().lower()
    factory = PROVIDERS.get(name)
    if factory is None:
        raise ProviderNotConfigured(f"Unknown STT_PROVIDER={name!r}; expected one of: {', '.join(sorted(PROVIDERS))}")
    return factory(settings)
