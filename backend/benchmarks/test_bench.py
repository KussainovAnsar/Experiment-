"""Unit tests for the benchmark helpers (no models, no audio).

Run explicitly — ``pytest.ini`` limits collection to ``tests/``::

    cd backend && .venv/bin/python -m pytest benchmarks/test_bench.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

pytest.importorskip("jiwer")
pytest.importorskip("soundfile")

import stt_bench  # noqa: E402
import utterances  # noqa: E402


def test_normalize_lowercases_and_strips_punctuation() -> None:
    assert stt_bench.normalize("Жи-ши: пишем «И»!  Ещё раз.") == "жи ши пишем и еще раз"


def test_normalize_keeps_kazakh_letters() -> None:
    assert stt_bench.normalize("Түсінбедім, қайталап айтыңызшы?") == "түсінбедім қайталап айтыңызшы"


def test_normalize_is_idempotent() -> None:
    once = stt_bench.normalize("Предложный падеж — о ком? о чём?")
    assert stt_bench.normalize(once) == once


def test_subsets_have_expected_sizes() -> None:
    assert 25 <= len(utterances.RU) <= 40
    assert 25 <= len(utterances.KK) <= 40
    assert 25 <= len(utterances.MIXED) <= 40


def test_mixed_items_switch_language() -> None:
    for speaker, spans in utterances.MIXED:
        assert speaker in ("teacher", "student")
        langs = {lang for lang, _ in spans}
        assert langs == {"ru", "kk"}, spans
        assert utterances.mixed_text(spans)


def test_reference_texts_have_no_digits() -> None:
    texts = utterances.RU + utterances.KK + [utterances.mixed_text(s) for _, s in utterances.MIXED]
    assert not any(ch.isdigit() for t in texts for ch in t)


def test_model_aliases_cover_both_backends() -> None:
    for name, per_backend in stt_bench.MODEL_ALIASES.items():
        assert set(per_backend) == set(stt_bench.BACKENDS), name


def test_default_matrix_has_forced_and_auto_for_mixed() -> None:
    modes = {lang for subset, cond, lang in stt_bench.DEFAULT_MATRIX if subset == "mixed" and cond == "clean"}
    assert modes == {"ru", "kk", None}
