"""Local STT benchmark: Whisper family on ru / kk / code-switched classroom speech.

What it measures
----------------
* WER and CER (jiwer) after text normalisation: lowercase, ``ё -> е``, punctuation stripped,
  whitespace collapsed.
* Real-time factor (RTF = decode seconds / audio seconds) on CPU, int8 weights.
* Language forcing (``ru`` / ``kk``) vs automatic language detection, with the distribution of
  detected languages on the mixed subset.
* Clean vs "degraded" (band-limited + noise) audio.

Backends
--------
``faster-whisper`` (CTranslate2, models from Hugging Face) is the backend used by the production
``whisper_local`` provider. When Hugging Face is unreachable the benchmark falls back to
``whisper-onnx``: the *same* OpenAI Whisper weights exported to ONNX (int8) by the sherpa-onnx
project and hosted on GitHub releases, driven by our own byte-correct greedy decoder
(:mod:`whisper_onnx`). The plain ``sherpa-onnx`` runtime is kept as a third option, but note that
its Python API drops byte-level BPE tokens, which deletes most Kazakh-specific letters from the
output. All backends use greedy decoding so that numbers are comparable.

Runs are executed sequentially and the script refuses to start a run whose projected duration
exceeds ``--max-run-minutes`` (estimated after the first few utterances); skipped runs are listed
in the results.

Usage::

    cd backend
    .venv/bin/python benchmarks/stt_bench.py --models tiny,base,small,medium,large-v3-turbo
    .venv/bin/python benchmarks/stt_bench.py --models tiny --limit 3   # smoke test

Outputs ``results/<backend>_<model>.json``, ``results/summary.json`` and ``results/RESULTS.md``.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gc
import json
import logging
import os
import platform
import re
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

import jiwer
import numpy as np
import soundfile as sf

log = logging.getLogger("stt_bench")

BENCH_DIR = Path(__file__).resolve().parent
DATA_DIR = BENCH_DIR / "data"
MANIFEST_PATH = DATA_DIR / "manifest.jsonl"
MODELS_DIR = BENCH_DIR / "models"
RESULTS_DIR = BENCH_DIR / "results"

MODEL_ALIASES: dict[str, dict[str, str]] = {
    # canonical -> per-backend name
    "tiny": {"faster-whisper": "tiny", "sherpa-onnx": "tiny", "whisper-onnx": "tiny"},
    "base": {"faster-whisper": "base", "sherpa-onnx": "base", "whisper-onnx": "base"},
    "small": {"faster-whisper": "small", "sherpa-onnx": "small", "whisper-onnx": "small"},
    "medium": {"faster-whisper": "medium", "sherpa-onnx": "medium", "whisper-onnx": "medium"},
    "large-v3-turbo": {"faster-whisper": "large-v3-turbo", "sherpa-onnx": "turbo", "whisper-onnx": "turbo"},
    "large-v3": {"faster-whisper": "large-v3", "sherpa-onnx": "large-v3", "whisper-onnx": "large-v3"},
}
SHERPA_ASR_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-whisper-{name}.tar.bz2"

# (subset, condition, language or None for auto-detect)
DEFAULT_MATRIX: list[tuple[str, str, str | None]] = [
    ("ru", "clean", "ru"),
    ("ru", "clean", None),
    ("ru", "degraded", "ru"),
    ("kk", "clean", "kk"),
    ("kk", "clean", None),
    ("mixed", "clean", "ru"),
    ("mixed", "clean", "kk"),
    ("mixed", "clean", None),
    ("mixed", "degraded", "ru"),
    ("mixed", "degraded", None),
]


# --------------------------------------------------------------------------- text normalisation

_PUNCT_RE = re.compile(r"[^\w\s]|_", flags=re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, ё→е, drop punctuation (hyphens become spaces), collapse whitespace."""
    text = text.lower().replace("ё", "е")
    text = _PUNCT_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()


# --------------------------------------------------------------------------- data


@dataclass
class Utterance:
    id: str
    subset: str
    condition: str
    speaker: str
    text: str
    audio: Path
    duration_sec: float


def load_manifest(limit: int | None = None) -> list[Utterance]:
    """Read ``data/manifest.jsonl``; ``limit`` keeps the first N items of every subset/condition."""
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"{MANIFEST_PATH} missing — run make_dataset.py first")
    items: list[Utterance] = []
    per_group: Counter[tuple[str, str]] = Counter()
    with MANIFEST_PATH.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            d = json.loads(line)
            key = (d["subset"], d["condition"])
            if limit is not None and per_group[key] >= limit:
                continue
            per_group[key] += 1
            items.append(
                Utterance(
                    id=d["id"], subset=d["subset"], condition=d["condition"], speaker=d["speaker"],
                    text=d["text"], audio=DATA_DIR / d["audio"], duration_sec=float(d["duration_sec"]),
                )
            )
    return items


def read_audio(path: Path) -> tuple[np.ndarray, int]:
    audio, sr = sf.read(str(path), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio, int(sr)


# --------------------------------------------------------------------------- backends


class Recognizer(Protocol):
    """One loaded model, fixed language mode."""

    def transcribe(self, audio: np.ndarray, sr: int) -> tuple[str, str | None]:
        """Return ``(text, detected_language)``."""
        ...


class Backend(Protocol):
    name: str
    version: str

    def available(self, model: str) -> bool: ...

    def load(self, model: str, language: str | None, threads: int) -> Recognizer: ...


class FasterWhisperBackend:
    """CTranslate2 Whisper (production provider). Needs Hugging Face access for model download."""

    name = "faster-whisper"

    def __init__(self) -> None:
        import faster_whisper  # noqa: F401

        self.version = getattr(faster_whisper, "__version__", "unknown")
        self._root = MODELS_DIR / "faster-whisper"
        self._root.mkdir(parents=True, exist_ok=True)

    def available(self, model: str) -> bool:
        from faster_whisper import WhisperModel

        try:
            WhisperModel(model, device="cpu", compute_type="int8", download_root=str(self._root), cpu_threads=1)
            return True
        except Exception as exc:
            log.warning("faster-whisper model %s unavailable: %s", model, type(exc).__name__)
            return False

    def load(self, model: str, language: str | None, threads: int) -> Recognizer:
        from faster_whisper import WhisperModel

        wm = WhisperModel(model, device="cpu", compute_type="int8", download_root=str(self._root), cpu_threads=threads)

        class _R:
            def transcribe(self, audio: np.ndarray, sr: int) -> tuple[str, str | None]:
                if sr != 16_000:
                    raise ValueError("faster-whisper backend expects 16 kHz audio")
                segments, info = wm.transcribe(
                    audio, language=language, beam_size=1, vad_filter=False, condition_on_previous_text=False
                )
                text = " ".join(s.text.strip() for s in segments)
                return text, info.language

        return _R()


class SherpaOnnxBackend:
    """OpenAI Whisper weights exported to ONNX (int8) by the sherpa-onnx project."""

    name = "sherpa-onnx"

    def __init__(self) -> None:
        import sherpa_onnx

        self.version = getattr(sherpa_onnx, "__version__", "unknown")
        self._root = MODELS_DIR / "whisper-onnx"
        self._root.mkdir(parents=True, exist_ok=True)

    def _dir(self, model: str) -> Path:
        return self._root / f"sherpa-onnx-whisper-{model}"

    def _files(self, model: str) -> tuple[Path, Path, Path]:
        d = self._dir(model)
        enc = d / f"{model}-encoder.int8.onnx"
        dec = d / f"{model}-decoder.int8.onnx"
        return enc, dec, d / f"{model}-tokens.txt"

    def available(self, model: str) -> bool:
        if all(p.exists() for p in self._files(model)):
            return True
        return self._download(model)

    def _download(self, model: str) -> bool:
        import shutil
        import tarfile
        import urllib.request

        url = SHERPA_ASR_URL.format(name=model)
        tmp = self._root / f"{model}.tar.bz2"
        log.info("downloading %s", url)
        try:
            with urllib.request.urlopen(url, timeout=3600) as r, tmp.open("wb") as f:  # noqa: S310
                shutil.copyfileobj(r, f)
            with tarfile.open(tmp, "r:bz2") as tf:
                tf.extractall(self._root, filter="data")
            return all(p.exists() for p in self._files(model))
        except Exception as exc:
            log.warning("sherpa-onnx model %s download failed: %s", model, type(exc).__name__)
            return False
        finally:
            tmp.unlink(missing_ok=True)

    def load(self, model: str, language: str | None, threads: int) -> Recognizer:
        import sherpa_onnx

        enc, dec, tok = self._files(model)
        rec = sherpa_onnx.OfflineRecognizer.from_whisper(
            encoder=str(enc), decoder=str(dec), tokens=str(tok),
            language=language or "", task="transcribe", num_threads=threads, decoding_method="greedy_search",
        )

        class _R:
            def transcribe(self, audio: np.ndarray, sr: int) -> tuple[str, str | None]:
                stream = rec.create_stream()
                stream.accept_waveform(sr, audio)
                rec.decode_stream(stream)
                res = stream.result
                lang = getattr(res, "lang", "") or None
                if lang and lang.startswith("<|"):
                    lang = lang.strip("<|>")
                return res.text.strip(), lang

        return _R()


class WhisperOnnxBackend(SherpaOnnxBackend):
    """sherpa-onnx ONNX exports driven by our own byte-correct decoder (see :mod:`whisper_onnx`)."""

    name = "whisper-onnx"

    def __init__(self) -> None:
        super().__init__()
        import onnxruntime

        self.version = f"onnxruntime {onnxruntime.__version__}"
        self._models: dict[str, object] = {}

    def load(self, model: str, language: str | None, threads: int) -> Recognizer:
        from whisper_onnx import WhisperOnnx

        # one shared model per backend; its encoder cache lets several language modes reuse an encode
        if model not in self._models:
            self._models.clear()
            self._models[model] = WhisperOnnx(self._dir(model), model, threads=threads)
        wm = self._models[model]

        class _R:
            def transcribe(self, audio: np.ndarray, sr: int) -> tuple[str, str | None]:
                return wm.transcribe_audio(audio, sr, language)

        return _R()


BACKENDS: dict[str, type] = {
    "faster-whisper": FasterWhisperBackend,
    "whisper-onnx": WhisperOnnxBackend,
    "sherpa-onnx": SherpaOnnxBackend,
}


def pick_backend(name: str) -> Backend:
    """Instantiate the requested backend, or the first working one for ``auto``."""
    order = list(BACKENDS) if name == "auto" else [name]
    errors: list[str] = []
    for n in order:
        try:
            b: Backend = BACKENDS[n]()
        except Exception as exc:
            errors.append(f"{n}: {type(exc).__name__}: {exc}")
            continue
        if name != "auto" or b.available("tiny"):
            return b
        errors.append(f"{n}: model download failed")
    raise RuntimeError("no usable STT backend: " + "; ".join(errors))


# --------------------------------------------------------------------------- runs


@dataclass
class UttResult:
    id: str
    ref: str
    hyp: str
    detected_lang: str | None
    decode_sec: float


@dataclass
class RunResult:
    model: str
    backend: str
    subset: str
    condition: str
    language_mode: str  # "forced:ru" | "forced:kk" | "auto"
    n: int = 0
    audio_sec: float = 0.0
    decode_sec: float = 0.0
    rtf: float | None = None
    wer: float | None = None
    cer: float | None = None
    detected_languages: dict[str, int] = field(default_factory=dict)
    skipped: bool = False
    skip_reason: str | None = None
    rtf_decode_only: bool = False  # encoder output reused from the previous language mode
    utterances: list[UttResult] = field(default_factory=list)


def run_group(
    backend: Backend,
    model: str,
    backend_model: str,
    utts: list[Utterance],
    languages: list[str | None],
    threads: int,
    max_run_sec: float,
) -> list[RunResult]:
    """Transcribe ``utts`` (one subset/condition) in every language mode and score each mode.

    Modes are the inner loop so that a backend with an encoder cache (``whisper-onnx``) encodes
    each clip once. ``rtf`` therefore includes encoding only for the first mode of the group; the
    other modes report decode-only time and are marked ``rtf_decode_only``.
    """
    subset, condition = utts[0].subset, utts[0].condition
    results: list[RunResult] = []
    recs: list[Recognizer] = []
    t_load = time.perf_counter()
    for language in languages:
        mode = f"forced:{language}" if language else "auto"
        rr = RunResult(model=model, backend=backend.name, subset=subset, condition=condition, language_mode=mode)
        rr.audio_sec = round(sum(u.duration_sec for u in utts), 2)
        results.append(rr)
        recs.append(backend.load(backend_model, language, threads))
    load_sec = time.perf_counter() - t_load
    log.info("[%s/%s %s] %d recognizers loaded in %.1fs", model, subset, condition, len(recs), load_sec)

    probe_n = min(3, len(utts))
    skipped_reason: str | None = None
    t_start = time.perf_counter()
    group_decode = 0.0
    for i, u in enumerate(utts):
        audio, sr = read_audio(u.audio)
        for rr, rec in zip(results, recs, strict=True):
            t0 = time.perf_counter()
            hyp, lang = rec.transcribe(audio, sr)
            dsec = time.perf_counter() - t0
            rr.utterances.append(
                UttResult(id=u.id, ref=u.text, hyp=hyp, detected_lang=lang, decode_sec=round(dsec, 3))
            )
            rr.decode_sec += dsec
            group_decode += dsec
        if i + 1 == probe_n and len(utts) > probe_n:
            probe_audio = sum(x.duration_sec for x in utts[:probe_n])
            projected = group_decode / max(probe_audio, 1e-6) * sum(x.duration_sec for x in utts)
            if projected > max_run_sec:
                skipped_reason = (
                    f"projected {projected / 60:.1f} min for {subset}/{condition} ({len(recs)} modes) exceeds limit "
                    f"{max_run_sec / 60:.0f} min (group RTF≈{group_decode / probe_audio:.2f} "
                    f"after {probe_n} utterances)"
                )
                log.warning("skipping %s/%s: %s", subset, condition, skipped_reason)
                break
    elapsed = time.perf_counter() - t_start

    for idx, rr in enumerate(results):
        if skipped_reason:
            rr.skipped, rr.skip_reason, rr.utterances = True, skipped_reason, []
            continue
        refs = [normalize(x.ref) for x in rr.utterances]
        hyps = [normalize(x.hyp) for x in rr.utterances]
        rr.n = len(refs)
        rr.rtf = round(rr.decode_sec / rr.audio_sec, 3) if rr.audio_sec else None
        rr.rtf_decode_only = idx > 0 and backend.name == "whisper-onnx"
        rr.wer = round(float(jiwer.wer(refs, hyps)), 4)
        rr.cer = round(float(jiwer.cer(refs, hyps)), 4)
        rr.detected_languages = dict(Counter(x.detected_lang or "?" for x in rr.utterances).most_common())
        rr.decode_sec = round(rr.decode_sec, 2)
        log.info(
            "[%s/%s %s %s] n=%d WER=%.3f CER=%.3f RTF=%.3f%s langs=%s",
            model, subset, condition, rr.language_mode, rr.n, rr.wer, rr.cer, rr.rtf or 0,
            " (decode only)" if rr.rtf_decode_only else "", rr.detected_languages,
        )
    log.info("[%s/%s %s] group done in %.0fs", model, subset, condition, elapsed)
    del recs
    gc.collect()
    return results


def run_model(
    backend: Backend,
    model: str,
    utts: list[Utterance],
    matrix: list[tuple[str, str, str | None]],
    threads: int,
    max_run_sec: float,
) -> dict:
    """Run the whole experiment matrix for one model and return a JSON-serialisable dict."""
    backend_model = MODEL_ALIASES[model][backend.name]
    out: dict = {
        "model": model, "backend": backend.name, "backend_version": backend.version, "backend_model": backend_model,
        "compute_type": "int8", "threads": threads, "runs": [], "started_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    if not backend.available(backend_model):
        out["skipped"] = True
        out["skip_reason"] = "model not available (download failed)"
        return out
    t0 = time.perf_counter()
    groups: dict[tuple[str, str], list[str | None]] = {}
    for subset, condition, language in matrix:
        groups.setdefault((subset, condition), []).append(language)
    abort_reason: str | None = None
    for (subset, condition), languages in groups.items():
        sel = [u for u in utts if u.subset == subset and u.condition == condition]
        if not sel:
            continue
        if abort_reason:
            for lang in languages:
                out["runs"].append(asdict(RunResult(
                    model=model, backend=backend.name, subset=subset, condition=condition,
                    language_mode=f"forced:{lang}" if lang else "auto", skipped=True,
                    skip_reason="skipped after earlier projection: " + abort_reason,
                )))
            continue
        group = run_group(backend, model, backend_model, sel, languages, threads, max_run_sec)
        out["runs"].extend(asdict(rr) for rr in group)
        if group and group[0].skipped and group[0].skip_reason and "RTF" in group[0].skip_reason:
            # same model, same hardware: every later group would be skipped for the same reason
            abort_reason = group[0].skip_reason
    out["elapsed_sec"] = round(time.perf_counter() - t0, 1)
    return out


# --------------------------------------------------------------------------- reporting


def _fmt(x: float | None, pct: bool = True) -> str:
    if x is None:
        return "—"
    return f"{x * 100:.1f}%" if pct else f"{x:.2f}"


def write_summary(model_results: list[dict], env: dict, out_dir: Path = RESULTS_DIR) -> None:
    """Write ``summary.json`` and ``RESULTS.md`` from per-model result dicts."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for mr in model_results:
        for r in mr.get("runs", []):
            rows.append({k: v for k, v in r.items() if k != "utterances"})
    summary = {"environment": env, "models": [
        {k: v for k, v in mr.items() if k != "runs"} for mr in model_results
    ], "runs": rows}
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines: list[str] = []
    lines.append("# Результаты локального STT-бенчмарка (синтетическая речь, CPU, int8)\n")
    backends = sorted({f"{mr['backend']} {mr.get('backend_version')}" for mr in model_results})
    lines.append(
        f"Сгенерировано: {env['generated_at']} · CPU: {env['cpu_count']} ядер · backend: {', '.join(backends)}\n"
    )
    lines.append("Все числа — WER/CER после нормализации (нижний регистр, ё→е, без пунктуации). "
                 "RTF = время декодирования / длительность аудио (меньше — быстрее). "
                 "**Аудио синтетическое (Piper TTS) — реальные записи Zoom дадут заметно худший WER.**\n")

    models = [(mr["model"], mr["backend"]) for mr in model_results]
    multi_backend = len({b for _, b in models}) > 1
    pivot_cols = [
        ("ru", "clean", "forced:ru", "ru чист. (ru)"),
        ("ru", "degraded", "forced:ru", "ru деград. (ru)"),
        ("kk", "clean", "forced:kk", "kk чист. (kk)"),
        ("mixed", "clean", "auto", "mixed чист. (auto)"),
        ("mixed", "clean", "forced:ru", "mixed чист. (ru)"),
        ("mixed", "clean", "forced:kk", "mixed чист. (kk)"),
        ("mixed", "degraded", "auto", "mixed деград. (auto)"),
    ]
    by_key = {(r["model"], r["backend"], r["subset"], r["condition"], r["language_mode"]): r for r in rows}

    lines.append("## Сводная таблица WER\n")
    lines.append("| Модель | " + " | ".join(c[3] for c in pivot_cols) + " | RTF (ru чист.) |")
    lines.append("|---|" + "---|" * (len(pivot_cols) + 1))
    for m, b in models:
        cells = []
        for s, c, lm, _ in pivot_cols:
            r = by_key.get((m, b, s, c, lm))
            cells.append("пропущено" if r and r.get("skipped") else _fmt(r["wer"]) if r else "—")
        r0 = by_key.get((m, b, "ru", "clean", "forced:ru"))
        cells.append(_fmt(r0["rtf"], pct=False) if r0 and r0.get("rtf") is not None else "—")
        label = f"{m} ({b})" if multi_backend else m
        lines.append(f"| {label} | " + " | ".join(cells) + " |")

    lines.append("\n## Все прогоны\n")
    lines.append("| Модель | Backend | Подмножество | Условие | Язык | n | WER | CER | RTF | Определённые языки |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        head = f"| {r['model']} | {r['backend']} | {r['subset']} | {r['condition']} | {r['language_mode']} |"
        if r.get("skipped"):
            lines.append(f"{head} — | пропущено | — | — | {r.get('skip_reason', '')} |")
            continue
        langs = ", ".join(f"{k}:{v}" for k, v in r["detected_languages"].items())
        lines.append(
            f"{head} {r['n']} | {_fmt(r['wer'])} | {_fmt(r['cer'])} | {_fmt(r['rtf'], pct=False)} | {langs} |"
        )

    lines.append("\n## Распределение определённого языка на mixed (auto-detect, чистое аудио)\n")
    lines.append("| Модель | Определённые языки (код:число реплик) |")
    lines.append("|---|---|")
    for m, b in models:
        r = by_key.get((m, b, "mixed", "clean", "auto"))
        if r and not r.get("skipped"):
            label = f"{m} ({b})" if multi_backend else m
            lines.append(f"| {label} | " + ", ".join(f"{k}:{v}" for k, v in r["detected_languages"].items()) + " |")
    if any(b == "sherpa-onnx" for _, b in models):
        lines.append(
            "\n> Оговорка о backend `sherpa-onnx`: его Python API возвращает пустые строки для байтовых BPE-токенов, "
            "поэтому казахские буквы ұ ө ә ң ғ ү һ (часто и қ) выпадают из гипотез — WER/CER по kk и mixed у этого "
            "backend завышены. Backend `whisper-onnx` (те же ONNX-веса, собственный байтовый декодер) "
            "этой проблемы не имеет."
        )
    skipped = [mr for mr in model_results if mr.get("skipped")] + [r for r in rows if r.get("skipped")]
    if skipped:
        lines.append("\n## Пропущено\n")
        for s in skipped:
            where = f"{s.get('model')} {s.get('subset', '')} {s.get('condition', '')} {s.get('language_mode', '')}"
            lines.append(f"- {where}: {s.get('skip_reason')}")
    (out_dir / "RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def environment_info(backend: Backend, threads: int) -> dict:
    return {
        "generated_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "threads": threads,
        "backend": backend.name,
        "backend_version": backend.version,
        "compute_type": "int8",
        "decoding": "greedy",
        "dataset_info": json.loads((DATA_DIR / "dataset_info.json").read_text(encoding="utf-8"))
        if (DATA_DIR / "dataset_info.json").exists() else None,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", default="auto", choices=["auto", *BACKENDS])
    ap.add_argument("--models", default="tiny,base,small,medium,large-v3-turbo",
                    help="comma-separated canonical names: " + ",".join(MODEL_ALIASES))
    ap.add_argument("--subsets", default="ru,kk,mixed")
    ap.add_argument("--conditions", default="clean,degraded")
    ap.add_argument("--threads", type=int, default=max(1, min(4, os.cpu_count() or 1)))
    ap.add_argument("--max-run-minutes", type=float, default=25.0,
                    help="skip a run if its projected duration exceeds this")
    ap.add_argument("--limit", type=int, default=None, help="utterances per subset/condition (smoke test)")
    ap.add_argument("--out", type=Path, default=RESULTS_DIR)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    unknown = [m for m in models if m not in MODEL_ALIASES]
    if unknown:
        ap.error(f"unknown models: {unknown}")
    subsets = {s.strip() for s in args.subsets.split(",")}
    conditions = {c.strip() for c in args.conditions.split(",")}
    matrix = [(s, c, lang) for s, c, lang in DEFAULT_MATRIX if s in subsets and c in conditions]

    utts = load_manifest(limit=args.limit)
    backend = pick_backend(args.backend)
    env = environment_info(backend, args.threads)
    log.info("backend=%s %s, %d utterances, models=%s", backend.name, backend.version, len(utts), models)

    results: list[dict] = []
    for m in models:
        t0 = time.perf_counter()
        mr = run_model(backend, m, utts, matrix, args.threads, args.max_run_minutes * 60)
        out_path = out_dir / f"{backend.name}_{m}.json"
        out_path.write_text(json.dumps(mr, ensure_ascii=False, indent=1), encoding="utf-8")
        results.append(mr)
        log.info("model %s finished in %.1f min", m, (time.perf_counter() - t0) / 60)
        # include previously saved models (other invocations) in the summary
        write_summary(_collect_results(results, out_dir), env, out_dir)
    return 0


def _collect_results(fresh: list[dict], out_dir: Path) -> list[dict]:
    """Merge fresh results with per-model JSON files already on disk (any backend)."""
    fresh_keys = {(r["backend"], r["model"]) for r in fresh}
    merged = list(fresh)
    for p in sorted(out_dir.glob("*_*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        known = d.get("model") in MODEL_ALIASES and d.get("backend") in BACKENDS
        if known and (d["backend"], d["model"]) not in fresh_keys:
            merged.append(d)
    order, border = list(MODEL_ALIASES), list(BACKENDS)
    merged.sort(key=lambda d: (border.index(d["backend"]), order.index(d["model"])))
    return merged


if __name__ == "__main__":
    sys.exit(main())
