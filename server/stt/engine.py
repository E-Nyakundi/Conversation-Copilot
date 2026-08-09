"""
faster-whisper wrapper.

Loaded lazily and once per process (model load is the expensive part —
we do NOT want to re-instantiate WhisperModel per WebSocket connection,
let alone per chunk). Import of faster_whisper itself is also deferred
into get_engine() so that `manage.py check` / `makemigrations` and the
rest of the Django app don't hard-require torch/ctranslate2 to be
installed just to boot.
"""

import os
import threading
import time
from pathlib import Path

import numpy as np

_engine = None
_engine_lock = threading.Lock()


def _hf_cache_has_model(model_size: str) -> bool:
    """Best-effort check of whether this Whisper model is already sitting
    in the Hugging Face cache (~/.cache/huggingface/hub), so warmup_models
    can tell you whether the next step is a real download or a fast local
    load — instead of leaving you guessing at a blank terminal."""
    cache_root = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    if not cache_root.exists():
        return False
    # faster-whisper's default CTranslate2 models are published under the
    # "Systran/faster-whisper-<size>" repo naming convention.
    needle = f"models--Systran--faster-whisper-{model_size}"
    return any(needle in p.name for p in cache_root.glob("models--*"))


class WhisperEngine:
    def __init__(self, model_size: str, device: str, compute_type: str, default_language: str | None = None):
        from faster_whisper import WhisperModel  # deferred import, see module docstring

        cached = _hf_cache_has_model(model_size)
        print(
            f"[stt.engine] Loading faster-whisper '{model_size}' model "
            f"({'found in HF cache, should be fast' if cached else 'NOT in HF cache yet — downloading now, this can take a few minutes on first run'})...",
            flush=True,
        )
        started = time.monotonic()
        self.model = WhisperModel(model_size, device=device, compute_type=compute_type)
        elapsed = time.monotonic() - started
        print(f"[stt.engine] Whisper model '{model_size}' ready in {elapsed:.1f}s.", flush=True)
        # Auto language-ID on short (1-7s) segments is unreliable, especially
        # at beam_size=1/int8 — it was observed flipping between English,
        # Italian, French, Swahili, Shona, and German at 17-50% confidence
        # across consecutive segments from continuous English speech. A
        # wrong language guess produces garbage or empty text, which the
        # consumer then silently drops. Fixing a known language (default
        # 'en', WHISPER_LANGUAGE env override) skips per-segment guessing
        # entirely unless a caller explicitly asks for auto-detect.
        self.default_language = default_language
        print(
            f"[stt.engine] Default transcription language: "
            f"{self.default_language or 'auto-detect (per-segment guessing)'}",
            flush=True,
        )

    def transcribe(self, audio: np.ndarray, language: str | None = "__default__") -> dict:
        """
        audio: mono float32 PCM in [-1, 1] at 16kHz.
        language: ISO 639-1 code to force, or None to auto-detect. Omit
        entirely (the "__default__" sentinel) to use this engine's
        configured default_language instead of auto-detecting every call.
        Returns {"text": str, "confidence": float | None} where confidence
        is a rough proxy derived from segment avg_logprob (higher = better,
        typically ranges roughly -1.0 to 0.0 for reasonable transcriptions).
        """
        if audio.size == 0:
            return {"text": "", "confidence": None}

        effective_language = self.default_language if language == "__default__" else language

        segments, _info = self.model.transcribe(
            audio,
            language=effective_language,
            beam_size=1,
            vad_filter=False,  # we run our own VAD upstream (audio/vad.py)
            condition_on_previous_text=False,
        )

        texts = []
        weighted_logprobs = []
        total_duration = 0.0
        for seg in segments:
            texts.append(seg.text.strip())
            if seg.avg_logprob is not None:
                duration = seg.end - seg.start
                weighted_logprobs.append(seg.avg_logprob * duration)
                total_duration += duration

        text = " ".join(t for t in texts if t).strip()
        confidence = sum(weighted_logprobs) / total_duration if weighted_logprobs and total_duration > 0 else None
        return {"text": text, "confidence": confidence}


def get_engine() -> WhisperEngine:
    """Thread-safe lazy singleton. Config comes from env so you can swap
    model size without touching code (see .env.example)."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                model_size = os.environ.get("WHISPER_MODEL_SIZE", "small")
                device = os.environ.get("WHISPER_DEVICE", "cpu")
                compute_type = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
                # Empty string explicitly opts back into per-segment
                # auto-detect (not recommended for short segments — see
                # the note in WhisperEngine.__init__).
                lang_env = os.environ.get("WHISPER_LANGUAGE", "en").strip()
                default_language = lang_env or None
                _engine = WhisperEngine(model_size, device, compute_type, default_language=default_language)
    return _engine


def pcm16_bytes_to_float32(raw: bytes) -> np.ndarray:
    """Convert little-endian int16 PCM bytes to float32 [-1, 1] mono array."""
    if not raw:
        return np.zeros(0, dtype=np.float32)
    ints = np.frombuffer(raw, dtype="<i2")
    return (ints.astype(np.float32)) / 32768.0
