"""
Hybrid STT router — Groq Whisper (cloud, primary) with the local
faster-whisper engine (stt/engine.py) as fallback on network failure,
missing key, or rate limit. Mirrors ai/cloud_router.py's shape exactly
(lazy singleton, deferred import, specific-exception fallback only) so
the two hybrid paths in this app stay consistent.

Deliberately used for FINAL segments only (audio/consumers.py's
_finalize_segment) — that's the only STT call left in the pipeline now
(the old live "partial" preview, which re-decoded locally on a timer,
was removed; see the note where PARTIAL_INTERVAL_S used to live in
audio/consumers.py for why).

Provider selection is left abstracted (STT_PROVIDER env var, default
"groq") even though Groq is the only cloud path implemented right now —
same reasoning as ai/cloud_router.py deferring OpenRouter/Gemini: if
Deepgram or AssemblyAI is added later (real streaming STT, not
chunk-based like Groq's), it plugs in as another branch in
_transcribe_cloud() without touching audio/consumers.py at all.
"""

import io
import logging
import os
import threading
import wave

import numpy as np

logger = logging.getLogger(__name__)

_groq_client = None
_groq_lock = threading.Lock()


def _get_groq_client():
    """Thread-safe lazy singleton, same shape as ai.cloud_router._get_groq_client()."""
    global _groq_client
    if _groq_client is None:
        with _groq_lock:
            if _groq_client is None:
                from groq import Groq  # deferred import — see module docstring

                api_key = os.environ.get("GROQ_API_KEY", "")
                if not api_key:
                    raise RuntimeError("GROQ_API_KEY is not set")
                _groq_client = Groq(api_key=api_key)
    return _groq_client


def _float32_to_wav_bytes(audio: np.ndarray, sample_rate: int = 16000) -> bytes:
    """Groq's audio.transcriptions endpoint wants a file, not a raw
    array — wrap the same mono float32 [-1, 1] PCM this app already
    passes to faster-whisper into an in-memory WAV container. stdlib
    `wave` only, no new dependency."""
    ints16 = np.clip(audio * 32768.0, -32768, 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(ints16.tobytes())
    buf.seek(0)
    return buf.read()


def _transcribe_via_groq(audio: np.ndarray, language: str | None) -> dict:
    """
    Raises on any Groq-side failure — get_final_transcription() decides
    whether to fall back. Returns {"text": str, "confidence": None} —
    Groq's transcription response doesn't expose a per-segment logprob
    like faster-whisper's does, so confidence is left None rather than
    inventing one; audio/consumers.py already treats None as "no
    confidence data" (same as stt/engine.py returns for empty audio).
    """
    from groq import APIConnectionError, APIError, APITimeoutError, RateLimitError

    client = _get_groq_client()
    model = os.environ.get("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")
    wav_bytes = _float32_to_wav_bytes(audio)

    # Same free vocabulary-biasing nudge as stt/engine.py's
    # WHISPER_INITIAL_PROMPT (shared env var, so setting it once covers
    # both paths) — previously only wired into the local faster-whisper
    # fallback, even though Groq is the actual STT path live traffic
    # hits (STT_PROVIDER=groq, local engine only used on Groq failure).
    # Groq's audio.transcriptions endpoint accepts the same `prompt`
    # param as OpenAI's Whisper API.
    prompt = os.environ.get("WHISPER_INITIAL_PROMPT", "").strip() or None

    try:
        response = client.audio.transcriptions.create(
            file=("segment.wav", wav_bytes),
            model=model,
            language=language,
            prompt=prompt,
        )
    except (APIConnectionError, APITimeoutError, RateLimitError, APIError) as e:
        raise RuntimeError(f"Groq transcription failed: {e}") from e

    text = (response.text or "").strip()
    return {"text": text, "confidence": None}


def get_final_transcription(audio: np.ndarray, language: str | None = None) -> dict:
    """
    Public entry point for audio/consumers.py._finalize_segment. Same
    return shape as stt.engine.WhisperEngine.transcribe(): {"text": str,
    "confidence": float | None}.

    language: ISO 639-1 code or None. Unlike stt.engine's "__default__"
    sentinel (which reads WHISPER_LANGUAGE), this passes language
    straight through — callers wanting the configured default should
    read WHISPER_LANGUAGE themselves and pass it in, since Groq's API
    doesn't share stt.engine's env-driven default plumbing.

    Falls back to the local engine (stt.engine.get_engine().transcribe)
    on empty audio, missing STT_PROVIDER cloud key, or any Groq-specific
    failure — never raises up to the caller for those cases, matching
    ai.cloud_router.get_suggestion()'s behavior.
    """
    if audio.size == 0:
        return {"text": "", "confidence": None}

    provider = os.environ.get("STT_PROVIDER", "groq").strip().lower()

    if provider == "groq" and os.environ.get("GROQ_API_KEY"):
        try:
            return _transcribe_via_groq(audio, language)
        except Exception:
            logger.warning(
                "Groq transcription failed, falling back to local Whisper",
                exc_info=True,
            )
    elif provider == "groq":
        logger.debug("STT_PROVIDER=groq but GROQ_API_KEY not set, using local Whisper directly")
    elif provider != "local":
        logger.warning("Unknown STT_PROVIDER=%r, falling back to local Whisper", provider)

    from stt.engine import get_engine

    return get_engine().transcribe(audio)
