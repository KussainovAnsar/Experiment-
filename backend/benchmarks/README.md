# STT benchmark — Whisper on ru / kk / code-switched classroom speech

Local, reproducible comparison of open-source speech-to-text models on the kind of speech LessonLens
processes in the pilot (a Russian-language lesson in a Kazakh school: teacher explains in Russian,
student answers in Kazakh, both switch languages mid-sentence). Results, methodology and the cloud
provider research are summarised in Russian in [`docs/PROVIDERS.md`](../../docs/PROVIDERS.md).

Everything here runs on CPU without any API keys. **The audio is synthetic (Piper TTS)** — scores
are an upper bound and a relative comparison, not a prediction of accuracy on real Zoom recordings.

## Layout

```
benchmarks/
  utterances.py           reference texts: RU (teacher), KK (student), MIXED (code-switched spans)
  make_dataset.py         Piper TTS -> 16 kHz wav + ffmpeg "degraded" variant -> data/manifest.jsonl
  stt_bench.py            runs Whisper models (faster-whisper / whisper-onnx / sherpa-onnx), WER/CER/RTF, language detection
  whisper_onnx.py         byte-correct greedy Whisper decoder over the sherpa-onnx ONNX exports (onnxruntime)
  test_bench.py           unit tests for normalisation and dataset invariants (run explicitly)
  requirements-bench.txt  benchmark-only Python deps
  results/                committed: RESULTS.md, summary.json, <backend>_<model>.json (per-utterance hyps), run_log_*.txt
  data/                   gitignored: generated audio + manifest (~30 MB)
  models/                 gitignored: Piper voices (~300 MB) and Whisper weights (up to ~7 GB)
```

## Reproduce

```bash
cd backend
.venv/bin/pip install -r benchmarks/requirements-bench.txt

# 1. synthesise the dataset (downloads 3 Piper voices on first run: HF, then GitHub mirror)
.venv/bin/python benchmarks/make_dataset.py --force

# 2. smoke test (3 utterances per subset, whisper-tiny, ~15 s)
.venv/bin/python benchmarks/stt_bench.py --models tiny --limit 3 --out /tmp/stt-smoke

# 3. full run, models sequentially, each run capped at 25 min projected duration
.venv/bin/python benchmarks/stt_bench.py --models tiny,base,small,medium,large-v3-turbo

# useful flags
#   --backend faster-whisper|sherpa-onnx|auto   (auto = faster-whisper if HF reachable, else sherpa-onnx)
#   --subsets ru,kk,mixed  --conditions clean,degraded  --threads 4  --max-run-minutes 25
```

Outputs land in `benchmarks/results/`: `RESULTS.md` (tables, Russian), `summary.json` (all runs
without hypotheses) and one JSON per model with every reference/hypothesis pair and detected language.
Re-running a subset of models merges with the JSON files already on disk.

### Backends

* `faster-whisper` is what the production `whisper_local` provider uses; its CTranslate2 models are
  downloaded from Hugging Face.
* `whisper-onnx` (default fallback) runs the same OpenAI Whisper weights exported to ONNX (int8) by
  `k2-fsa/sherpa-onnx` (GitHub release tag `asr-models`) through our own small decoder in
  `whisper_onnx.py`: Whisper-exact log-mel features, language detection, greedy search, and
  byte-level reassembly of BPE tokens. It was used for the committed results because Hugging Face is
  blocked in the build environment.
* `sherpa-onnx` is the stock runtime for the same files. Its Python API returns an empty string for
  byte-fallback tokens, so Kazakh-specific letters (ұ ө ә ң ғ ү һ, often қ) vanish from hypotheses —
  Kazakh WER/CER are inflated. Kept for comparison; do not use it for kk.

All backends use greedy decoding, int8 weights and 4 CPU threads so numbers are comparable. Model
name `large-v3-turbo` maps to `turbo` in the ONNX exports. The ONNX decoder caps output length
relative to audio duration and stops on 3× repeated n-grams (greedy Whisper has no temperature
fallback; small models loop on Kazakh).

### Dataset details

| subset | speaker | n | audio | TTS voice |
|---|---|---|---|---|
| ru/clean | teacher | 32 | 134 s | `ru_RU-irina-medium`, `ru_RU-dmitri-medium` |
| kk/clean | student | 30 | 60 s | `kk_KZ-issai-high` (speakers 0, 2, 4, 5) |
| mixed/clean | both | 30 | 103 s | ru spans of the teacher: Russian voices; everything else: `kk_KZ-issai-high` |
| ru/degraded, mixed/degraded | — | 62 | 237 s | ffmpeg: `aresample=16000, highpass=250, lowpass=3400, anoisesrc(pink, 0.02) amix` |

Manifest record (`data/manifest.jsonl`):

```json
{"id": "mixed_000", "subset": "mixed", "condition": "clean", "speaker": "student", "language": "mixed",
 "text": "Апай, предложный падеж деген не?", "audio": "clean/mixed/mixed_000.wav", "duration_sec": 3.1,
 "sample_rate": 16000, "tts": "piper:kk_KZ-issai-high#0", "spans": [["kk", "Апай,"], ["ru", "предложный падеж"], ["kk", "деген не?"]]}
```

The same format works for real recordings: put wav files + reference texts in a manifest and point
`stt_bench.py` at it (`MANIFEST_PATH` in the script, or symlink `data/`).

Normalisation before scoring: lowercase, `ё→е`, every non-word character (including hyphens) becomes a
space, whitespace collapsed. Kazakh-specific letters (ә, ғ, қ, ң, ө, ұ, ү, һ, і) are kept as-is.

## Observed runtime (4 vCPU, 15 GB RAM, int8)

<!-- RUNTIME_BEGIN -->
| model | backend | wall time (10 runs incl. model loads) | RTF ru/clean | RTF kk/clean | skipped runs |
|---|---|---|---|---|---|
| tiny | whisper-onnx | 1.7 min | 0.133 | 0.229 | — |
| base | whisper-onnx | 3.1 min | 0.195 | 0.419 | — |
| small | whisper-onnx | 9.0 min | 0.542 | 1.15 | — |
| medium | whisper-onnx | 35.3 min | 1.982 | 4.326 | — |
| large-v3-turbo | whisper-onnx | 10.4 min | 0.776 | 1.61 | — |
| tiny | sherpa-onnx | 1.7 min | 0.089 | 0.112 | — |
| base | sherpa-onnx | 2.6 min | 0.134 | 0.179 | — |
| small | sherpa-onnx | 7.4 min | 0.41 | 0.551 | — |
| medium | sherpa-onnx | 28.4 min | 1.599 | 2.355 | — |

Total benchmark wall time: 100 min for 9 model/backend combinations (matrix of 10 runs each, ~17 min of audio per combination). RTF = decode time / audio duration, excluding model load. Models were run sequentially; nothing else was running on the machine.
<!-- RUNTIME_END -->

The `sherpa-onnx` backend was run only up to `medium`: once the byte-token defect was confirmed the
run was stopped to save CPU time (`whisper-onnx` is the primary backend). Per-run log lines are in
`results/run_log_whisper-onnx.txt` and `results/run_log_sherpa-onnx.txt`.

Dataset synthesis: ~1 min 40 s for 154 files (Piper on CPU). Model downloads: tiny 245 MB, base 433 MB,
small 1.3 GB, medium 3.8 GB (fp32 + int8 in the archive), large-v3-turbo 990 MB (int8 only).

## Not covered (and why)

* Cloud providers (OpenAI, Deepgram, AssemblyAI, Google, ElevenLabs, Yandex, Azure, AWS) — no keys in
  the development environment; see the research tables in `docs/PROVIDERS.md`.
* wav2vec2 / Kazakh fine-tunes of Whisper — hosted on Hugging Face only, unreachable here.
* Diarization — every file is a single speaker by construction.
