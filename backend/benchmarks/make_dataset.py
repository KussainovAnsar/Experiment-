"""Build the synthetic ru / kk / mixed classroom-speech dataset for the STT benchmark.

Pipeline
--------
1. Make sure Piper voices are present under ``models/piper`` (Hugging Face first, then the
   sherpa-onnx GitHub mirror, which re-hosts the same ``.onnx`` + ``.onnx.json`` files).
   If no Piper voice can be obtained, fall back to ``espeak-ng`` when it is installed.
2. Synthesise every utterance of :mod:`utterances` (ru teacher, kk student, mixed) at the
   voice's native rate, then convert to 16 kHz mono PCM (the same format the production
   pipeline feeds to STT).
3. Build a "degraded" copy of the ru and mixed subsets with ffmpeg: 16 kHz, light coloured
   noise mixed in, 250 Hz high-pass and 3.4 kHz low-pass — a crude imitation of a poor
   Zoom/Meet link.
4. Write ``data/manifest.jsonl`` (one record per audio file) and ``data/dataset_info.json``.

Honest caveat: TTS audio is clean, evenly paced and has no overlaps, hesitations or room
acoustics. Scores on it are an upper bound for real lesson recordings.

Usage::

    cd backend
    .venv/bin/python benchmarks/make_dataset.py [--force] [--no-degraded]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utterances import KK, MIXED, RU, mixed_text  # noqa: E402

log = logging.getLogger("make_dataset")

BENCH_DIR = Path(__file__).resolve().parent
DATA_DIR = BENCH_DIR / "data"
MODELS_DIR = BENCH_DIR / "models" / "piper"
MANIFEST_PATH = DATA_DIR / "manifest.jsonl"
INFO_PATH = DATA_DIR / "dataset_info.json"

TARGET_SR = 16_000
SPAN_GAP_SEC = 0.12  # silence between code-switched spans

FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")

# voice name -> (HF path inside rhasspy/piper-voices, sherpa-onnx GitHub tarball name)
VOICES: dict[str, tuple[str, str]] = {
    "ru_RU-irina-medium": ("ru/ru_RU/irina/medium/ru_RU-irina-medium", "vits-piper-ru_RU-irina-medium"),
    "ru_RU-dmitri-medium": ("ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium", "vits-piper-ru_RU-dmitri-medium"),
    "kk_KZ-issai-high": ("kk/kk_KZ/issai/high/kk_KZ-issai-high", "vits-piper-kk_KZ-issai-high"),
}
HF_REPO = "rhasspy/piper-voices"
SHERPA_TTS_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/{name}.tar.bz2"

# kk_KZ-issai-high is multi-speaker; ids come from its .onnx.json speaker_id_map
KK_SPEAKERS_STUDENT = [0, 2, 4, 5]  # ISSAI_KazakhTTS2_M2, F3, F1, F2
KK_SPEAKER_TEACHER = 3  # ISSAI_KazakhTTS_F1_Raya


@dataclass
class ManifestItem:
    """One audio file of the dataset. Written as a JSONL line."""

    id: str
    subset: str  # ru | kk | mixed
    condition: str  # clean | degraded
    speaker: str  # teacher | student
    language: str  # ru | kk | mixed
    text: str
    audio: str  # path relative to benchmarks/data
    duration_sec: float
    sample_rate: int
    tts: str  # engine/voice(s) that produced the audio
    spans: list[list[str]] | None = None  # [[lang, text], ...] for mixed


# --------------------------------------------------------------------------- voices


def _find_voice(name: str) -> Path | None:
    hits = sorted(MODELS_DIR.glob(f"**/{name}.onnx"))
    for h in hits:
        if h.with_suffix(".onnx.json").exists() or Path(str(h) + ".json").exists():
            return h
    return None


def _download_from_hf(name: str, hf_path: str) -> bool:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        return False
    dst = MODELS_DIR / name
    dst.mkdir(parents=True, exist_ok=True)
    try:
        for ext in (".onnx", ".onnx.json"):
            f = hf_hub_download(HF_REPO, hf_path + ext)
            shutil.copy(f, dst / (name + ext))
        return True
    except Exception as exc:  # network / policy errors are expected in some environments
        log.warning("hf download failed for %s: %s", name, type(exc).__name__)
        shutil.rmtree(dst, ignore_errors=True)
        return False


def _download_from_sherpa_mirror(name: str, tar_name: str) -> bool:
    url = SHERPA_TTS_URL.format(name=tar_name)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MODELS_DIR / (tar_name + ".tar.bz2")
    try:
        with urllib.request.urlopen(url, timeout=600) as r, tmp.open("wb") as f:  # noqa: S310
            shutil.copyfileobj(r, f)
        with tarfile.open(tmp, "r:bz2") as tf:
            tf.extractall(MODELS_DIR, filter="data")
        return _find_voice(name) is not None
    except Exception as exc:
        log.warning("sherpa mirror download failed for %s: %s", name, type(exc).__name__)
        return False
    finally:
        tmp.unlink(missing_ok=True)


def ensure_voices() -> dict[str, Path]:
    """Return ``{voice_name: path_to_onnx}`` for every voice that is available locally.

    Missing voices are downloaded (Hugging Face, then GitHub mirror). Voices that cannot be
    obtained are simply absent from the result; the caller decides how to fall back.
    """
    found: dict[str, Path] = {}
    for name, (hf_path, tar_name) in VOICES.items():
        p = _find_voice(name)
        if p is None:
            log.info("voice %s missing, downloading", name)
            ok = _download_from_hf(name, hf_path) or _download_from_sherpa_mirror(name, tar_name)
            if ok:
                p = _find_voice(name)
        if p is not None:
            found[name] = p
    return found


# --------------------------------------------------------------------------- synthesis backends


class PiperTTS:
    """Thin wrapper around piper-tts voices; returns float32 PCM at the voice sample rate."""

    def __init__(self, voices: dict[str, Path]) -> None:
        from piper import PiperVoice

        self._voices = {name: PiperVoice.load(str(p)) for name, p in voices.items()}

    @property
    def names(self) -> list[str]:
        return list(self._voices)

    def sample_rate(self, voice: str) -> int:
        return int(self._voices[voice].config.sample_rate)

    def synth(self, text: str, voice: str, speaker_id: int | None = None) -> np.ndarray:
        from piper.config import SynthesisConfig

        v = self._voices[voice]
        cfg = SynthesisConfig(speaker_id=speaker_id) if speaker_id is not None else None
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            v.synthesize_wav(text, w, syn_config=cfg)
        buf.seek(0)
        with wave.open(buf, "rb") as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        return pcm.astype(np.float32) / 32768.0


class EspeakTTS:
    """Fallback: espeak-ng command line (robotic, but keeps the benchmark reproducible)."""

    SR = 22_050

    def __init__(self, binary: str) -> None:
        self._bin = binary

    def synth(self, text: str, lang: str) -> np.ndarray:
        out = subprocess.run(
            [self._bin, "-v", lang, "-s", "150", "--stdout", text],
            check=True,
            capture_output=True,
        ).stdout
        with wave.open(io.BytesIO(out), "rb") as w:
            sr = w.getframerate()
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        if sr != self.SR:
            pcm = _resample_linear(pcm, sr, self.SR)
        return pcm


def _resample_linear(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return x
    n_out = round(len(x) * sr_out / sr_in)
    xp = np.linspace(0.0, 1.0, num=len(x), endpoint=False)
    fp = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(fp, xp, x).astype(np.float32)


def _write_wav(path: Path, pcm: np.ndarray, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm16 = np.clip(pcm * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm16.tobytes())


def _to_16k(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", str(TARGET_SR),
         "-c:a", "pcm_s16le", str(dst)],
        check=True,
    )


def degrade(src: Path, dst: Path, seed: int) -> None:
    """Imitate a poor conferencing link: 16 kHz, band-limit 250–3400 Hz, light pink noise.

    Noise amplitude 0.02 against TTS speech peaks of roughly 0.5–0.9 gives an SNR of about
    20–25 dB — audible hiss but still intelligible for a human.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    filter_complex = (
        f"[0:a]aresample={TARGET_SR},highpass=f=250,lowpass=f=3400[a];"
        f"anoisesrc=color=pink:amplitude=0.02:sample_rate={TARGET_SR}:seed={seed}[n];"
        "[a][n]amix=inputs=2:duration=first:normalize=0[m];"
        "[m]volume=0.9[out]"
    )
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-i", str(src), "-filter_complex", filter_complex,
         "-map", "[out]", "-ac", "1", "-ar", str(TARGET_SR), "-c:a", "pcm_s16le", str(dst)],
        check=True,
    )


def _duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


# --------------------------------------------------------------------------- dataset builder


class DatasetBuilder:
    """Synthesises all subsets and writes the manifest."""

    def __init__(self, piper: PiperTTS | None, espeak: EspeakTTS | None) -> None:
        if piper is None and espeak is None:
            raise RuntimeError("No TTS backend available: install piper-tts voices or espeak-ng")
        self.piper = piper
        self.espeak = espeak
        self.items: list[ManifestItem] = []
        self.raw_dir = DATA_DIR / "raw"  # native-rate TTS output, not used by the benchmark

    # voice selection -------------------------------------------------------------------
    def _ru_voice(self, idx: int) -> str | None:
        if self.piper is None:
            return None
        ru = [n for n in self.piper.names if n.startswith("ru_RU")]
        return ru[idx % len(ru)] if ru else None

    def _kk_voice(self) -> str | None:
        if self.piper is None:
            return None
        kk = [n for n in self.piper.names if n.startswith("kk_KZ")]
        return kk[0] if kk else None

    def _synth_span(self, text: str, lang: str, speaker: str, idx: int) -> tuple[np.ndarray, int, str]:
        """Return (pcm, sample_rate, voice_label) for one span."""
        if self.piper is not None:
            if lang == "ru" and speaker == "teacher":
                voice = self._ru_voice(idx)
                if voice:
                    return self.piper.synth(text, voice), self.piper.sample_rate(voice), f"piper:{voice}"
            kk_voice = self._kk_voice()
            if kk_voice:
                # Kazakh voice for kk spans and for the (Kazakh) student's Russian: a Kazakh
                # accent on Russian words is exactly what the pilot will hear.
                if speaker == "teacher":
                    sid = KK_SPEAKER_TEACHER
                else:
                    sid = KK_SPEAKERS_STUDENT[idx % len(KK_SPEAKERS_STUDENT)]
                pcm = self.piper.synth(text, kk_voice, sid)
                return pcm, self.piper.sample_rate(kk_voice), f"piper:{kk_voice}#{sid}"
            ru_voice = self._ru_voice(idx)
            if ru_voice:
                return self.piper.synth(text, ru_voice), self.piper.sample_rate(ru_voice), f"piper:{ru_voice}"
        assert self.espeak is not None
        return self.espeak.synth(text, lang), EspeakTTS.SR, f"espeak-ng:{lang}"

    # subsets ---------------------------------------------------------------------------
    def _add(self, item_id: str, subset: str, speaker: str, language: str, text: str,
             spans: list[tuple[str, str]], idx: int) -> None:
        pieces: list[np.ndarray] = []
        voices: list[str] = []
        sr_out: int | None = None
        for lang, span_text in spans:
            pcm, sr, voice = self._synth_span(span_text, lang, speaker, idx)
            if sr_out is None:
                sr_out = sr
            if sr != sr_out:
                pcm = _resample_linear(pcm, sr, sr_out)
            if pieces:
                pieces.append(np.zeros(int(SPAN_GAP_SEC * sr_out), dtype=np.float32))
            pieces.append(pcm)
            if voice not in voices:
                voices.append(voice)
        assert sr_out is not None
        pcm_all = np.concatenate(pieces)
        raw = self.raw_dir / subset / f"{item_id}.wav"
        _write_wav(raw, pcm_all, sr_out)
        clean = DATA_DIR / "clean" / subset / f"{item_id}.wav"
        _to_16k(raw, clean)
        self.items.append(
            ManifestItem(
                id=item_id,
                subset=subset,
                condition="clean",
                speaker=speaker,
                language=language,
                text=text,
                audio=str(clean.relative_to(DATA_DIR)),
                duration_sec=round(_duration(clean), 3),
                sample_rate=TARGET_SR,
                tts="+".join(voices),
                spans=[[lang, t] for lang, t in spans] if language == "mixed" else None,
            )
        )

    def build_clean(self) -> None:
        for i, text in enumerate(RU):
            self._add(f"ru_{i:03d}", "ru", "teacher", "ru", text, [("ru", text)], i)
        for i, text in enumerate(KK):
            self._add(f"kk_{i:03d}", "kk", "student", "kk", text, [("kk", text)], i)
        for i, (speaker, spans) in enumerate(MIXED):
            self._add(f"mixed_{i:03d}", "mixed", speaker, "mixed", mixed_text(spans), spans, i)

    def build_degraded(self) -> None:
        for item in [it for it in self.items if it.condition == "clean" and it.subset in ("ru", "mixed")]:
            src = DATA_DIR / item.audio
            dst = DATA_DIR / "degraded" / item.subset / f"{item.id}.wav"
            seed = int.from_bytes(hashlib.sha1(item.id.encode()).digest()[:3], "big")
            degrade(src, dst, seed)
            self.items.append(
                ManifestItem(
                    **{**asdict(item), "condition": "degraded", "audio": str(dst.relative_to(DATA_DIR)),
                       "duration_sec": round(_duration(dst), 3), "tts": item.tts + "+ffmpeg-degraded"}
                )
            )

    def write(self, tts_engine: str, elapsed_sec: float) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with MANIFEST_PATH.open("w", encoding="utf-8") as f:
            for it in self.items:
                f.write(json.dumps(asdict(it), ensure_ascii=False) + "\n")
        summary: dict[str, object] = {
            "tts_engine": tts_engine,
            "sample_rate": TARGET_SR,
            "elapsed_sec": round(elapsed_sec, 1),
            "subsets": {},
        }
        for subset in ("ru", "kk", "mixed"):
            for cond in ("clean", "degraded"):
                sel = [it for it in self.items if it.subset == subset and it.condition == cond]
                if sel:
                    summary["subsets"][f"{subset}/{cond}"] = {  # type: ignore[index]
                        "n": len(sel),
                        "audio_sec": round(sum(it.duration_sec for it in sel), 1),
                    }
        INFO_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("wrote %s (%d items) and %s", MANIFEST_PATH, len(self.items), INFO_PATH)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="rebuild even if manifest exists")
    ap.add_argument("--no-degraded", action="store_true", help="skip the degraded variant")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if MANIFEST_PATH.exists() and not args.force:
        log.info("%s exists; use --force to rebuild", MANIFEST_PATH)
        return 0
    if shutil.which(FFMPEG) is None:
        log.error("ffmpeg not found")
        return 2

    t0 = time.time()
    piper: PiperTTS | None = None
    espeak: EspeakTTS | None = None
    voices = ensure_voices()
    if voices:
        try:
            piper = PiperTTS(voices)
        except Exception as exc:
            log.warning("piper-tts unusable (%s)", type(exc).__name__)
    if piper is None or not any(n.startswith("kk_KZ") for n in voices):
        esp = shutil.which("espeak-ng")
        if esp:
            espeak = EspeakTTS(esp)
            log.warning("falling back to espeak-ng for languages without a Piper voice")
    engine = ("piper-tts " + ",".join(sorted(voices))) if piper else ""
    if espeak:
        engine = (engine + " + espeak-ng") if engine else "espeak-ng"
    log.info("TTS engine: %s", engine)

    if DATA_DIR.exists():
        for sub in ("raw", "clean", "degraded"):
            shutil.rmtree(DATA_DIR / sub, ignore_errors=True)
    builder = DatasetBuilder(piper, espeak)
    builder.build_clean()
    if not args.no_degraded:
        builder.build_degraded()
    builder.write(engine, time.time() - t0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
