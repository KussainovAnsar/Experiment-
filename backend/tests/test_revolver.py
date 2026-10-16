"""Free-tier revolver (LLM + STT) on stubs: rotation on 429, cooldowns, keyless endpoints, exhaustion."""

from __future__ import annotations

import base64
import io
import json
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.pipeline import runner
from app.providers.llm import base as llm_base
from app.providers.llm.base import FreeLimitsExhausted, ImageInput, LLMError, LLMHTTPError, LLMResult
from app.providers.llm.mock import fallback_analysis
from app.providers.llm.openai import OpenAILLMProvider
from app.providers.llm.registry import get_llm_provider
from app.providers.llm.revolver import NO_FRAMES_ANALYSIS_NOTE, RevolverLLMProvider, thin_images
from app.providers.revolver import (
    CooldownBook,
    OrderEntry,
    best_retry_after,
    cooldown_for,
    parse_order,
    parse_retry_after,
    retry_hint_from_text,
)
from app.providers.stt import base as stt_base
from app.providers.stt.base import Segment, STTError, STTHTTPError, TranscriptResult
from app.providers.stt.openai import OpenAISTTProvider
from app.providers.stt.registry import get_stt_provider
from app.providers.stt.revolver import GROQ_STT_URL, RevolverSTTProvider

# ---------------------------------------------------------------- helpers


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_settings(**kw: Any) -> Settings:
    base = {"revolver_cooldown_sec": 600.0}
    base.update(kw)
    return Settings(_env_file=None, **base)


class StubLLM:
    """Scripted endpoint: each call pops the next outcome (exception instance or dict)."""

    def __init__(self, key: str, outcomes: list[Any], calls: list[tuple[str, int, str]]) -> None:
        self.key, self.outcomes, self.calls = key, outcomes, calls

    async def analyze(self, *, system: str, user_text: str, images: list[ImageInput], json_schema: dict) -> LLMResult:
        self.calls.append((self.key, len(images), user_text))
        outcome = self.outcomes.pop(0) if self.outcomes else {"manager_indicators": {"lesson_structure_notes": "ok"}}
        if isinstance(outcome, BaseException):
            raise outcome
        return LLMResult(data=json.loads(json.dumps(outcome)), model="raw-model")


def revolver(order: str, script: dict[str, list[Any]], clock: Clock | None = None, **keys: str):
    calls: list[tuple[str, int, str]] = []
    keys = keys or {"groq_api_key": "g", "gemini_api_key": "m", "openrouter_api_key": "o"}
    rev = RevolverLLMProvider(
        make_settings(revolver_llm_order=order, **keys),
        cooldowns=CooldownBook(clock or Clock()),
        provider_factory=lambda slot: StubLLM(slot.entry.key, script.setdefault(slot.entry.key, []), calls),
    )
    return rev, calls


def images(tmp_path: Path, n: int) -> list[ImageInput]:
    out = []
    for i in range(n):
        p = tmp_path / f"f{i}.jpg"
        p.write_bytes(b"\xff\xd8fake")
        out.append(ImageInput(path=p, label=f"Кадр #{i}"))
    return out


async def run(rev: RevolverLLMProvider, imgs: list[ImageInput] | None = None) -> LLMResult:
    return await rev.analyze(system="s", user_text="u", images=imgs or [], json_schema={})


# ---------------------------------------------------------------- shared plumbing


def test_parse_order_handles_free_suffix_and_image_caps() -> None:
    entries = parse_order(
        " groq:qwen/qwen3.8-27b@3, openrouter:google/gemma-4-31b-it:free@8,deepgram,groq:qwen/qwen3.8-27b "
    )
    assert entries == [
        OrderEntry("groq", "qwen/qwen3.8-27b", 3),
        OrderEntry("openrouter", "google/gemma-4-31b-it:free", 8),
        OrderEntry("deepgram", "", None),
    ]
    assert entries[2].key == "deepgram"


def test_retry_after_and_cooldown_policy() -> None:
    assert parse_retry_after({"Retry-After": "42"}) == 42.0
    assert parse_retry_after({"x": "1"}) is None
    assert retry_hint_from_text("Rate limit reached ... Please try again in 25.71s. Visit") == pytest.approx(25.71)
    assert retry_hint_from_text("Please try again in 7m12.5s") == pytest.approx(432.5)
    assert retry_hint_from_text('{"retryDelay": "37s"}') == 37.0
    assert retry_hint_from_text("no hint, 5ms") is None
    assert best_retry_after({"retry-after": "1"}, "try again in 25s") == 25.0
    assert cooldown_for(429, 30.0, 600) == 30.0
    assert cooldown_for(429, None, 600) == 600
    assert cooldown_for(503, None, 600) == 600
    assert cooldown_for(None, None, 600) == 600  # timeout / bad JSON
    assert cooldown_for(413, None, 600) is None  # request does not fit: skip, no cooldown


# ---------------------------------------------------------------- LLM revolver


async def test_llm_rotates_on_429_and_records_endpoint() -> None:
    rev, calls = revolver(
        "gemini:gemini-3.8-flash,groq:qwen/qwen3.8-27b",
        {"gemini:gemini-3.8-flash": [LLMHTTPError("rate", status_code=429, retry_after=30)]},
    )
    result = await run(rev)
    assert [c[0] for c in calls] == ["gemini:gemini-3.8-flash", "groq:qwen/qwen3.8-27b"]
    assert result.model == "groq:qwen/qwen3.8-27b"
    assert result.extra["revolver"]["skipped"] == ["gemini:gemini-3.8-flash: HTTP 429"]
    assert 29 < rev.cooldowns.remaining("gemini:gemini-3.8-flash") <= 30


async def test_llm_cooldown_skips_then_recovers() -> None:
    clock = Clock()
    rev, calls = revolver(
        "gemini:gemini-3.8-flash,groq:qwen/qwen3.8-27b",
        {"gemini:gemini-3.8-flash": [LLMHTTPError("boom", status_code=503)]},
        clock,
    )
    await run(rev)
    calls.clear()
    await run(rev)  # gemini is cooling down (default 600 s): not even called
    assert [c[0] for c in calls] == ["groq:qwen/qwen3.8-27b"]
    clock.now += 601
    calls.clear()
    result = await run(rev)
    assert result.model == "gemini:gemini-3.8-flash" and [c[0] for c in calls] == ["gemini:gemini-3.8-flash"]


async def test_llm_bad_json_cools_down_but_oversized_request_does_not() -> None:
    rev, _ = revolver(
        "gemini:gemini-3.8-flash,groq:qwen/qwen3.8-27b,openrouter:google/gemma-4-31b-it:free",
        {
            "gemini:gemini-3.8-flash": [LLMError("model output is not a JSON object")],
            "groq:qwen/qwen3.8-27b": [LLMHTTPError("too large", status_code=413)],
        },
    )
    result = await run(rev)
    assert result.model == "openrouter:google/gemma-4-31b-it:free"
    assert rev.cooldowns.remaining("gemini:gemini-3.8-flash") > 0
    assert rev.cooldowns.remaining("groq:qwen/qwen3.8-27b") == 0


def test_llm_skips_endpoints_without_key_and_paid_openrouter_models() -> None:
    rev, _ = revolver(
        "gemini:gemini-3.8-flash,mistral:mistral-medium-latest,openrouter:openai/gpt-5,groq:openai/gpt-oss-120b",
        {},
        groq_api_key="g",
        openrouter_api_key="o",
    )
    assert [s.entry.key for s in rev.slots] == ["groq:openai/gpt-oss-120b"]
    assert rev.slots[0].max_images == 0  # gpt-oss is text-only even without @0


def test_llm_without_any_key_is_not_configured() -> None:
    with pytest.raises(llm_base.ProviderNotConfigured, match="GEMINI_API_KEY"):
        RevolverLLMProvider(make_settings())
    with pytest.raises(llm_base.ProviderNotConfigured):
        get_llm_provider(make_settings(llm_provider="revolver"))


async def test_llm_all_failed_raises_free_limits_exhausted() -> None:
    rev, _ = revolver(
        "gemini:gemini-3.8-flash,groq:qwen/qwen3.8-27b",
        {
            "gemini:gemini-3.8-flash": [LLMHTTPError("rate", status_code=429, retry_after=120)],
            "groq:qwen/qwen3.8-27b": [TimeoutError("slow")],
        },
    )
    with pytest.raises(FreeLimitsExhausted) as info:
        await run(rev)
    assert "Бесплатные лимиты" in str(info.value) and "повторите позже" in str(info.value)
    assert 119 < info.value.retry_after_sec <= 120
    # The pipeline turns this into a delayed retry and shows the message as is.
    assert runner._retry_after_sec(info.value) == info.value.retry_after_sec
    assert runner.is_retryable(info.value)
    assert "Бесплатные лимиты" in runner._failure_message(runner.LessonStatus.analyzing, info.value)


async def test_llm_thins_frames_and_text_only_endpoint_marks_analysis(tmp_path: Path) -> None:
    imgs = images(tmp_path, 10)
    assert [i.label for i in thin_images(imgs, 3)] == ["Кадр #0", "Кадр #4", "Кадр #9"]  # even spread
    rev, calls = revolver(
        "groq:qwen/qwen3.8-27b@3,groq:openai/gpt-oss-120b",
        {"groq:qwen/qwen3.8-27b": [LLMHTTPError("rate", status_code=429)]},
    )
    result = await run(rev, imgs)
    assert calls[0][1] == 3 and "передано 3 из 10" in calls[0][2]
    assert calls[1][1] == 0 and "изображения кадров этой модели не переданы" in calls[1][2]
    assert result.model == "groq:openai/gpt-oss-120b [без кадров]"
    assert result.data["manager_indicators"]["lesson_structure_notes"].startswith(NO_FRAMES_ANALYSIS_NOTE)
    assert result.extra["revolver"]["frames_sent"] == 0


# ---------------------------------------------------------------- OpenAI-compatible adapter (no network)


def _fake_client(provider: OpenAILLMProvider, handler: Any) -> list[httpx.Request]:
    import openai

    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    provider._client = openai.AsyncOpenAI(
        api_key="k",
        base_url="https://api.groq.com/openai/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(wrapped)),
    )
    return seen


async def test_openai_compatible_429_carries_retry_after() -> None:
    provider = OpenAILLMProvider("k", "qwen/qwen3.8-27b", label="Groq", max_retries=0)
    _fake_client(
        provider, lambda r: httpx.Response(429, headers={"retry-after": "17"}, json={"error": {"message": "x"}})
    )
    with pytest.raises(LLMHTTPError) as info:
        await provider.analyze(system="s", user_text="u", images=[], json_schema={})
    assert info.value.status_code == 429 and info.value.retry_after == 17.0


async def test_openai_compatible_json_object_dialect(tmp_path: Path) -> None:
    from PIL import Image

    big = tmp_path / "big.png"
    Image.new("RGB", (3000, 1500), "white").save(big)
    payload = fallback_analysis().model_dump(mode="json")
    provider = OpenAILLMProvider(
        "k",
        "google/gemma-4-31b-it:free",
        structured="json_object",
        max_tokens_param="max_tokens",
        image_detail=None,
        image_max_px=800,
        max_tokens=4000,
    )
    seen = _fake_client(
        provider,
        lambda r: httpx.Response(
            200,
            json={
                "id": "1",
                "object": "chat.completion",
                "created": 0,
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": json.dumps(payload)},
                    }
                ],
            },
        ),
    )
    result = await provider.analyze(
        system="s", user_text="u", images=[ImageInput(path=big, label="Кадр #1")], json_schema={"type": "object"}
    )
    assert result.data["topic"] == payload["topic"]
    body = json.loads(seen[0].content)
    assert body["response_format"] == {"type": "json_object"} and body["max_tokens"] == 4000
    assert "max_completion_tokens" not in body and "Схема JSON" in body["messages"][0]["content"]
    image_part = body["messages"][1]["content"][2]["image_url"]
    assert "detail" not in image_part and image_part["url"].startswith("data:image/jpeg;base64,")
    raw = base64.b64decode(image_part["url"].split(",", 1)[1])
    assert max(Image.open(io.BytesIO(raw)).size) == 800


# ---------------------------------------------------------------- STT revolver


class StubSTT:
    def __init__(self, key: str, outcomes: list[Any], calls: list[str]) -> None:
        self.key, self.outcomes, self.calls = key, outcomes, calls
        self.name = key

    async def transcribe(self, audio_path: Path, *, language_hints: list[str]) -> TranscriptResult:
        self.calls.append(self.key)
        if self.outcomes:
            raise self.outcomes.pop(0)
        return TranscriptResult(provider=self.key, language="ru", segments=[Segment(0, 1, "unknown", "привет")])


def stt_revolver(script: dict[str, list[Any]], clock: Clock | None = None, **keys: str):
    calls: list[str] = []

    def factory(name: str):
        return lambda settings, model: StubSTT(f"{name}:{model}" if model else name, script.setdefault(name, []), calls)

    rev = RevolverSTTProvider(
        make_settings(**(keys or {"groq_api_key": "g", "deepgram_api_key": "d"})),
        cooldowns=CooldownBook(clock or Clock()),
        factories={n: factory(n) for n in ("groq", "deepgram", "assemblyai", "elevenlabs")},
    )
    return rev, calls


async def test_stt_rotates_from_groq_to_deepgram_and_cools_down(tmp_path: Path) -> None:
    clock = Clock()
    rev, calls = stt_revolver(
        {"groq": [STTHTTPError("rate", status_code=429, retry_after=60), STTHTTPError("rate", status_code=429)]},
        clock,
    )
    assert [e.key for e in rev.entries] == ["groq:whisper-large-v3", "groq:whisper-large-v3-turbo", "deepgram"]
    result = await rev.transcribe(tmp_path / "a.mp3", language_hints=["ru"])
    assert calls == ["groq:whisper-large-v3", "groq:whisper-large-v3-turbo", "deepgram"]
    assert result.provider == "deepgram"
    calls.clear()
    clock.now += 61  # large-v3 honoured Retry-After 60 s, turbo got the default 600 s
    result = await rev.transcribe(tmp_path / "a.mp3", language_hints=["ru"])
    assert calls == ["groq:whisper-large-v3"] and result.provider == "groq:whisper-large-v3"


async def test_stt_status_recovered_from_legacy_adapter_text(tmp_path: Path) -> None:
    rev, _ = stt_revolver({"groq": [STTError("Groq down")] * 2, "deepgram": [STTError("Deepgram HTTP 402: no credit")]})
    with pytest.raises(stt_base.FreeLimitsExhausted) as info:
        await rev.transcribe(tmp_path / "a.mp3", language_hints=["ru"])
    assert "Бесплатные лимиты распознавания речи исчерпаны" in str(info.value)
    assert rev.cooldowns.remaining("deepgram") > 0 and info.value.retry_after_sec > 0


def test_stt_without_keys_is_not_configured() -> None:
    with pytest.raises(stt_base.ProviderNotConfigured, match="GROQ_API_KEY"):
        RevolverSTTProvider(make_settings())
    with pytest.raises(stt_base.ProviderNotConfigured):
        get_stt_provider(make_settings(stt_provider="revolver"))


async def test_groq_whisper_adapter_chunks_and_maps_429(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wav = tmp_path / "a.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=16000:d=3", str(wav)],
        check=True,
    )
    seen: list[httpx.Request] = []
    replies = [
        httpx.Response(429, headers={"retry-after": "5"}, text="rate limited"),
        httpx.Response(200, json={"language": "russian", "segments": [{"start": 0.0, "end": 2.5, "text": "привет"}]}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return replies.pop(0)

    real_client = httpx.AsyncClient

    class MockClient(real_client):  # type: ignore[misc,valid-type]
        def __init__(self, *a: Any, **kw: Any) -> None:
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    provider = OpenAISTTProvider("k", model="whisper-large-v3", api_url=GROQ_STT_URL, name="groq", label="Groq")
    monkeypatch.setattr(httpx, "AsyncClient", MockClient)
    with pytest.raises(STTHTTPError) as info:
        await provider.transcribe(wav, language_hints=["ru"])
    assert info.value.status_code == 429 and info.value.retry_after == 5.0
    result = await provider.transcribe(wav, language_hints=["ru"])
    assert str(seen[0].url) == GROQ_STT_URL
    assert b'name="response_format"\r\n\r\nverbose_json' in seen[1].content
    assert result.provider == "groq" and result.segments[0].text == "привет"
