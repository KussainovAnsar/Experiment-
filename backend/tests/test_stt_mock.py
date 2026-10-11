"""Mock STT provider, speaker-role assignment helpers and the registry (no network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import get_settings
from app.providers.stt import mock as mock_mod
from app.providers.stt.base import (
    ProviderNotConfigured,
    Segment,
    STTProvider,
    TranscriptResult,
    Word,
    assign_speaker_roles,
    finalize,
    primary_language,
    segments_from_words,
)
from app.providers.stt.mock import MockSTTProvider, fallback_transcript
from app.providers.stt.registry import PROVIDERS, get_stt_provider


async def test_mock_fallback_without_fixture(tmp_path: Path) -> None:
    provider = MockSTTProvider(fixture=tmp_path / "missing.json")
    assert isinstance(provider, STTProvider)
    result = await provider.transcribe(tmp_path / "audio.wav", language_hints=["ru", "kk"])
    assert isinstance(result, TranscriptResult)
    assert result.provider == "mock"
    assert result.language == "ru"
    assert len(result.segments) >= 5
    assert {s.speaker for s in result.segments} == {"teacher", "student"}
    assert result.speaker_map == {"SPEAKER_00": "teacher", "SPEAKER_01": "student"}
    assert result.word_count > 20
    assert result.duration_sec == pytest.approx(41.0)
    assert "падежные" in result.full_text


async def test_mock_reads_fixture(tmp_path: Path) -> None:
    fixture = tmp_path / "transcript.json"
    fixture.write_text(
        json.dumps(
            {
                "provider": "mock",
                "language": "ru",
                "speaker_map": {"SPEAKER_00": "teacher", "SPEAKER_01": "student"},
                "segments": [
                    {"start": 0.0, "end": 4.2, "speaker": "teacher", "text": "Открываем учебник.", "confidence": 0.95},
                    {"start": 4.2, "end": 6.0, "speaker": "SPEAKER_01", "text": "Ашамын.", "confidence": 0.9},
                    {"start": 6.0, "end": 7.0, "speaker": "ghost", "text": "…"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = await MockSTTProvider(fixture=fixture).transcribe(tmp_path / "x.wav", language_hints=["ru"])
    assert [s.speaker for s in result.segments] == ["teacher", "student", "unknown"]
    assert result.segments[0].to_dict() == {
        "start": 0.0,
        "end": 4.2,
        "speaker": "teacher",
        "text": "Открываем учебник.",
        "confidence": 0.95,
    }
    assert result.full_text == "Открываем учебник. Ашамын. …"


async def test_mock_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = tmp_path / "t.json"
    fixture.write_text(json.dumps({"segments": [{"start": 0, "end": 1, "speaker": "teacher", "text": "Привет"}]}))
    monkeypatch.setenv("MOCK_TRANSCRIPT_PATH", str(fixture))
    assert mock_mod.fixture_path() == fixture
    result = await MockSTTProvider().transcribe(tmp_path / "x.wav", language_hints=[])
    assert result.full_text == "Привет"


async def test_mock_invalid_fixture_falls_back(tmp_path: Path) -> None:
    fixture = tmp_path / "broken.json"
    fixture.write_text("{not json")
    result = await MockSTTProvider(fixture=fixture).transcribe(tmp_path / "x.wav", language_hints=["kk"])
    assert result.language == "kk"
    assert result.full_text == fallback_transcript().full_text


def test_assign_speaker_roles_most_words_is_teacher() -> None:
    segs = [
        Segment(0, 5, "unknown", "Сегодня мы повторяем падежные окончания имён существительных"),
        Segment(5, 6, "unknown", "Да"),
        Segment(6, 10, "unknown", "Скажи пожалуйста какое окончание"),
        Segment(10, 11, "unknown", "к школе"),
    ]
    speaker_map = assign_speaker_roles(segs, ["B", "A", "B", "A"])
    assert speaker_map == {"B": "teacher", "A": "student"}
    assert [s.speaker for s in segs] == ["teacher", "student", "teacher", "student"]


def test_assign_speaker_roles_three_labels() -> None:
    segs = [Segment(0, 1, "unknown", "a b c d e"), Segment(1, 2, "unknown", "x"), Segment(2, 3, "unknown", "y z")]
    speaker_map = assign_speaker_roles(segs, ["0", "1", "2"])
    assert speaker_map == {"0": "teacher", "1": "student", "2": "student"}


def test_assign_speaker_roles_without_labels_is_unknown() -> None:
    segs = [Segment(0, 1, "teacher", "a"), Segment(1, 2, "student", "b")]
    assert assign_speaker_roles(segs, [None, None]) == {}
    assert [s.speaker for s in segs] == ["unknown", "unknown"]
    with pytest.raises(ValueError):
        assign_speaker_roles(segs, [None])


def test_segments_from_words_groups_by_speaker_and_pause() -> None:
    words = [
        Word("Открой", 0.0, 0.4, "S1"),
        Word("учебник.", 0.5, 1.0, "S1"),
        Word("Хорошо", 1.2, 1.6, "S2"),
        Word("Страница", 4.0, 4.5, "S1"),
        Word("пять", 4.6, 5.0, "S1", confidence=0.8),
    ]
    segs, labels = segments_from_words(words, max_gap_sec=1.2)
    assert [s.text for s in segs] == ["Открой учебник.", "Хорошо", "Страница пять"]
    assert labels == ["S1", "S2", "S1"]
    assert segs[2].confidence == pytest.approx(0.8)
    result = finalize("x", "ru", segs, labels)
    assert result.speaker_map == {"S1": "teacher", "S2": "student"}
    assert [s.speaker for s in result.segments] == ["teacher", "student", "teacher"]


def test_primary_language() -> None:
    assert primary_language(["ru", "kk"]) == "ru"
    assert primary_language([" KK "]) == "kk"
    assert primary_language([]) is None
    assert primary_language(None) is None


def test_registry_mock_and_unknown() -> None:
    settings = get_settings()
    assert isinstance(get_stt_provider(settings.model_copy(update={"stt_provider": "mock"})), MockSTTProvider)
    with pytest.raises(ProviderNotConfigured):
        get_stt_provider(settings.model_copy(update={"stt_provider": "nope"}))
    expected = {"mock", "whisper_local", "openai", "deepgram", "assemblyai", "google", "elevenlabs", "yandex"}
    assert set(PROVIDERS) == expected


@pytest.mark.parametrize("name", ["openai", "deepgram", "assemblyai", "google", "elevenlabs", "yandex"])
def test_http_providers_require_keys(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_STT_ACCESS_TOKEN", raising=False)
    settings = get_settings().model_copy(
        update={
            "stt_provider": name,
            "openai_api_key": "",
            "deepgram_api_key": "",
            "assemblyai_api_key": "",
            "elevenlabs_api_key": "",
            "google_application_credentials": "",
            "yandex_api_key": "",
            "yandex_folder_id": "",
        }
    )
    with pytest.raises(ProviderNotConfigured):
        get_stt_provider(settings)


def test_http_providers_construct_with_keys() -> None:
    settings = get_settings().model_copy(
        update={
            "openai_api_key": "k",
            "deepgram_api_key": "k",
            "assemblyai_api_key": "k",
            "elevenlabs_api_key": "k",
            "google_application_credentials": "/tmp/sa.json",
            "yandex_api_key": "k",
            "yandex_folder_id": "f",
        }
    )
    for name in ("openai", "deepgram", "assemblyai", "google", "elevenlabs", "yandex"):
        provider = get_stt_provider(settings.model_copy(update={"stt_provider": name}))
        assert provider.name == name
        assert isinstance(provider, STTProvider)
