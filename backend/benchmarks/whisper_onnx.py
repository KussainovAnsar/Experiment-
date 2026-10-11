"""Minimal, byte-correct Whisper decoder on top of the sherpa-onnx ONNX exports.

Why this exists
---------------
sherpa-onnx's Python API returns per-token *strings*. Whisper's vocabulary is byte-level BPE, and
most Kazakh-specific Cyrillic letters (ұ ө ә ң ғ ү һ, often қ) are emitted as two single-byte
tokens. sherpa-onnx renders each such byte token as an empty string, so these letters silently
disappear from the transcript ("Бұл сөз қай" -> "Бл сз к"). That inflates WER/CER on Kazakh for
reasons unrelated to the acoustic model.

This module runs the same encoder/decoder ONNX files with ``onnxruntime`` directly, keeps token
*bytes*, and decodes the concatenated byte string as UTF-8 at the end — exactly what OpenAI's
tokenizer does. It also exposes Whisper's language detection (argmax over language tokens after
``<|startoftranscript|>``). Greedy decoding only; no timestamps; one 30-second window (the benchmark
utterances are short).

Feature extraction follows ``whisper.audio.log_mel_spectrogram``: 16 kHz, n_fft 400, hop 160,
Slaney mel filterbank (80 or 128 bins, from model metadata), log10, dynamic-range clamp 8 dB,
``(x + 4) / 4`` scaling.

Decoding guards: Whisper's greedy search has no temperature fallback, so small models can loop
("Сын бедым бедым бедым…"). We cap the number of new tokens relative to the audio length and stop as
soon as the same 1–6-gram has been emitted three times in a row, keeping a single copy. This is a
crude stand-in for faster-whisper's compression-ratio fallback and only affects pathological cases.
"""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import numpy as np
import onnxruntime as ort

SAMPLE_RATE = 16_000
N_FFT = 400
HOP = 160
CHUNK_SAMPLES = 30 * SAMPLE_RATE
N_FRAMES = CHUNK_SAMPLES // HOP  # 3000
# Greedy Whisper has no temperature fallback; cap output length relative to the audio and stop on loops.
MAX_TOKENS_BASE = 24
MAX_TOKENS_PER_SEC = 12.0
LOOP_NGRAMS = (1, 2, 3, 4, 6)
LOOP_REPEATS = 3


def _looping(tokens: list[int]) -> int:
    """Return n > 0 if the last n-gram was emitted ``LOOP_REPEATS`` times in a row, else 0."""
    for n in LOOP_NGRAMS:
        if len(tokens) < n * LOOP_REPEATS:
            continue
        tail = tokens[-n:]
        if all(tokens[len(tokens) - n * (k + 1) : len(tokens) - n * k] == tail for k in range(1, LOOP_REPEATS)):
            return n
    return 0


# --------------------------------------------------------------------------- features


def _hz_to_mel_slaney(f: np.ndarray) -> np.ndarray:
    f_sp = 200.0 / 3
    mels = f / f_sp
    min_log_hz = 1000.0
    min_log_mel = min_log_hz / f_sp
    logstep = np.log(6.4) / 27.0
    log_t = f >= min_log_hz
    mels = np.where(log_t, min_log_mel + np.log(np.maximum(f, 1e-9) / min_log_hz) / logstep, mels)
    return mels


def _mel_to_hz_slaney(m: np.ndarray) -> np.ndarray:
    f_sp = 200.0 / 3
    freqs = f_sp * m
    min_log_hz = 1000.0
    min_log_mel = min_log_hz / f_sp
    logstep = np.log(6.4) / 27.0
    log_t = m >= min_log_mel
    return np.where(log_t, min_log_hz * np.exp(logstep * (m - min_log_mel)), freqs)


def mel_filterbank(n_mels: int, sr: int = SAMPLE_RATE, n_fft: int = N_FFT) -> np.ndarray:
    """librosa.filters.mel(sr, n_fft, n_mels) with htk=False, norm="slaney" — what Whisper ships."""
    fft_freqs = np.linspace(0, sr / 2, 1 + n_fft // 2)
    mel_pts = np.linspace(_hz_to_mel_slaney(np.array(0.0)), _hz_to_mel_slaney(np.array(sr / 2.0)), n_mels + 2)
    hz_pts = _mel_to_hz_slaney(mel_pts)
    fdiff = np.diff(hz_pts)
    ramps = hz_pts[:, None] - fft_freqs[None, :]
    weights = np.zeros((n_mels, len(fft_freqs)), dtype=np.float32)
    for i in range(n_mels):
        lower = -ramps[i] / fdiff[i]
        upper = ramps[i + 2] / fdiff[i + 1]
        weights[i] = np.maximum(0, np.minimum(lower, upper))
    enorm = 2.0 / (hz_pts[2 : n_mels + 2] - hz_pts[:n_mels])
    weights *= enorm[:, None]
    return weights


def log_mel_spectrogram(audio: np.ndarray, filters: np.ndarray) -> np.ndarray:
    """Return ``(n_mels, 3000)`` features for one padded/trimmed 30-second window."""
    x = audio.astype(np.float32)
    if len(x) > CHUNK_SAMPLES:
        x = x[:CHUNK_SAMPLES]
    else:
        x = np.pad(x, (0, CHUNK_SAMPLES - len(x)))
    # torch.stft(center=True, pad_mode="reflect")
    x = np.pad(x, (N_FFT // 2, N_FFT // 2), mode="reflect")
    window = np.hanning(N_FFT + 1)[:-1].astype(np.float32)  # periodic Hann, as torch.hann_window
    n_frames = 1 + (len(x) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n_frames)[:, None]
    frames = x[idx] * window
    spec = np.fft.rfft(frames, axis=1)
    mag = (np.abs(spec[:-1]) ** 2).T  # drop last frame -> (201, 3000)
    mel = filters @ mag
    log_spec = np.log10(np.maximum(mel, 1e-10))
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    return ((log_spec + 4.0) / 4.0).astype(np.float32)


# --------------------------------------------------------------------------- model


class WhisperOnnx:
    """Encoder + greedy decoder for a sherpa-onnx Whisper export directory."""

    def __init__(self, model_dir: Path, model: str, threads: int = 4, int8: bool = True) -> None:
        suffix = ".int8.onnx" if int8 else ".onnx"
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        providers = ["CPUExecutionProvider"]
        self.encoder = ort.InferenceSession(str(model_dir / f"{model}-encoder{suffix}"), so, providers=providers)
        self.decoder = ort.InferenceSession(str(model_dir / f"{model}-decoder{suffix}"), so, providers=providers)
        meta = self.encoder.get_modelmeta().custom_metadata_map
        self.n_mels = int(meta["n_mels"])
        self.n_text_layer = int(meta["n_text_layer"])
        self.n_text_ctx = int(meta["n_text_ctx"])
        self.n_text_state = int(meta["n_text_state"])
        self.sot = int(meta["sot"])
        self.eot = int(meta["eot"])
        self.transcribe = int(meta["transcribe"])
        self.no_timestamps = int(meta["no_timestamps"])
        self.no_speech = int(meta["no_speech"])
        self.blank = int(meta["blank_id"])
        self.lang_tokens = [int(t) for t in meta["all_language_tokens"].split(",")]
        self.lang_codes = meta["all_language_codes"].split(",")
        self.lang_to_token = dict(zip(self.lang_codes, self.lang_tokens, strict=True))
        self.token_to_lang = dict(zip(self.lang_tokens, self.lang_codes, strict=True))
        self.non_speech = [int(t) for t in meta["non_speech_tokens"].split(",") if t]
        self.filters = mel_filterbank(self.n_mels)
        self.id_to_bytes = self._load_tokens(model_dir / f"{model}-tokens.txt")
        self._timestamp_begin = self.no_timestamps + 1
        # The benchmark decodes the same clip in several language modes back to back; the encoder
        # output does not depend on the language, so keep the last one (capacity 1 — a medium
        # cross-attention cache is ~300 MB).
        self._enc_cache: tuple[str, np.ndarray, np.ndarray] | None = None

    @staticmethod
    def _load_tokens(path: Path) -> dict[int, bytes]:
        out: dict[int, bytes] = {}
        with path.open(encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) == 2:
                    out[int(parts[1])] = base64.b64decode(parts[0])
        return out

    # ---- inference helpers
    def _encode(self, audio: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        key = hashlib.blake2b(np.ascontiguousarray(audio, dtype=np.float32).tobytes(), digest_size=16).hexdigest()
        if self._enc_cache is not None and self._enc_cache[0] == key:
            return self._enc_cache[1], self._enc_cache[2]
        mel = log_mel_spectrogram(audio, self.filters)[None]  # (1, n_mels, 3000)
        k, v = self.encoder.run(None, {"mel": mel})
        self._enc_cache = (key, k, v)
        return k, v

    def _empty_cache(self) -> np.ndarray:
        return np.zeros((self.n_text_layer, 1, self.n_text_ctx, self.n_text_state), dtype=np.float32)

    def _step(self, tokens: list[int], k_cache, v_cache, cross_k, cross_v, offset: int):
        logits, k_cache, v_cache = self.decoder.run(
            None,
            {
                "tokens": np.array([tokens], dtype=np.int64),
                "in_n_layer_self_k_cache": k_cache,
                "in_n_layer_self_v_cache": v_cache,
                "n_layer_cross_k": cross_k,
                "n_layer_cross_v": cross_v,
                "offset": np.array([offset], dtype=np.int64),
            },
        )
        return logits[0, -1], k_cache, v_cache

    def detect_language(self, cross_k: np.ndarray, cross_v: np.ndarray) -> str:
        """Whisper language ID: argmax over language tokens given only <|startoftranscript|>."""
        logits, _, _ = self._step([self.sot], self._empty_cache(), self._empty_cache(), cross_k, cross_v, 0)
        lang_logits = logits[self.lang_tokens]
        return self.lang_codes[int(np.argmax(lang_logits))]

    def transcribe_audio(self, audio: np.ndarray, sr: int, language: str | None = None) -> tuple[str, str]:
        """Return ``(text, language)``; ``language=None`` triggers auto-detection."""
        if sr != SAMPLE_RATE:
            raise ValueError(f"expected {SAMPLE_RATE} Hz audio, got {sr}")
        cross_k, cross_v = self._encode(audio)
        lang = language or self.detect_language(cross_k, cross_v)
        if lang not in self.lang_to_token:
            raise ValueError(f"unknown language code {lang!r}")
        prompt = [self.sot, self.lang_to_token[lang], self.transcribe, self.no_timestamps]
        k_cache, v_cache = self._empty_cache(), self._empty_cache()
        logits, k_cache, v_cache = self._step(prompt, k_cache, v_cache, cross_k, cross_v, 0)
        tokens: list[int] = []
        offset = len(prompt)
        audio_sec = len(audio) / sr
        max_new = min(self.n_text_ctx - len(prompt) - 1, int(MAX_TOKENS_BASE + MAX_TOKENS_PER_SEC * audio_sec))
        for i in range(max_new):
            logits = logits.astype(np.float32)
            logits[self.non_speech] = -np.inf
            logits[self._timestamp_begin :] = -np.inf  # no timestamp tokens in no_timestamps mode
            logits[self.eot + 1 : self._timestamp_begin] = -np.inf  # other special tokens
            if i == 0:
                logits[self.blank] = -np.inf
            nxt = int(np.argmax(logits))
            if nxt == self.eot:
                break
            tokens.append(nxt)
            n = _looping(tokens)
            if n:
                # degenerate repetition (typical for small models on low-resource languages): keep one copy
                del tokens[len(tokens) - n * (LOOP_REPEATS - 1) :]
                break
            logits, k_cache, v_cache = self._step([nxt], k_cache, v_cache, cross_k, cross_v, offset)
            offset += 1
        text = b"".join(self.id_to_bytes.get(t, b"") for t in tokens).decode("utf-8", errors="replace")
        return text.strip(), lang
