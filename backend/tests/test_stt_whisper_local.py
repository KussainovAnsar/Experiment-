"""whisper_local speed knobs: greedy by default, batched pipeline on demand, all cores — with a fake faster_whisper."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from app.providers.stt import whisper_local


class _Seg:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start, self.end, self.text, self.avg_logprob = start, end, text, -0.2


class _Info:
    language, language_probability = "ru", 0.97


@pytest.fixture()
def fake_fw(monkeypatch: pytest.MonkeyPatch) -> dict:
    calls: dict = {}

    class WhisperModel:
        def __init__(self, name: str, **kw) -> None:
            calls["model"] = (name, kw)

        def transcribe(self, path: str, **kw):  # noqa: ANN201
            calls["sequential"] = kw
            return iter([_Seg(0.0, 1.5, " Здравствуйте "), _Seg(1.5, 2.0, "  ")]), _Info()

    class BatchedInferencePipeline:
        def __init__(self, model) -> None:  # noqa: ANN001
            self.model = model

        def transcribe(self, path: str, **kw):  # noqa: ANN201
            calls["batched"] = kw
            return iter([_Seg(0.0, 1.0, "Сәлем")]), _Info()

    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = WhisperModel
    mod.BatchedInferencePipeline = BatchedInferencePipeline
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    monkeypatch.setattr(whisper_local, "_MODEL_CACHE", {})
    return calls


async def test_cpu_defaults_are_greedy_sequential_all_cores(fake_fw: dict, tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF")
    provider = whisper_local.WhisperLocalProvider("large-v3-turbo", device="cpu")
    result = await provider.transcribe(audio, language_hints=["ru", "kk"])
    assert [s.text for s in result.segments] == ["Здравствуйте"]
    assert fake_fw["sequential"]["beam_size"] == 1 and fake_fw["sequential"]["vad_filter"] is True
    name, kw = fake_fw["model"]
    assert name == "large-v3-turbo" and kw["compute_type"] == "int8" and kw["cpu_threads"] >= 1


async def test_batched_pipeline_when_batch_size_set(
    fake_fw: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WHISPER_BATCH_SIZE", "8")
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF")
    result = await whisper_local.WhisperLocalProvider("small", device="cpu").transcribe(audio, language_hints=["kk"])
    assert result.segments[0].text == "Сәлем"
    assert fake_fw["batched"]["batch_size"] == 8 and "sequential" not in fake_fw


def test_cuda_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("WHISPER_BATCH_SIZE", "WHISPER_COMPUTE_TYPE", "WHISPER_BEAM_SIZE"):
        monkeypatch.delenv(var, raising=False)
    provider = whisper_local.WhisperLocalProvider("large-v3-turbo", device="cuda")
    assert (provider.compute_type, provider.batch_size, provider.beam_size) == ("float16", 16, 1)
