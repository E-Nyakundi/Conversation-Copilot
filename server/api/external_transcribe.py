"""
Stateless transcription endpoint for external callers (Vontran) —
added 2026-09-03.

Deliberately separate from rest_views.py: everything in that file
serves copilot's own session/turn/suggestion model, authenticated via
accounts.auth.token_required (per end-user AuthToken). This endpoint
has nothing to do with Sessions, Turns, or the live VAD pipeline — a
caller sends one audio file, gets one transcript back, no session is
created or touched. Giving Vontran a copilot *user* account and
AuthToken so it could call token_required-protected views would have
mixed two unrelated auth surfaces (copilot's own users vs. a sibling
backend service) for no real benefit, so this uses its own
env-var-configured shared secret instead (EXTERNAL_TRANSCRIBE_API_KEY)
— a service credential, not a user credential.

Reuses stt.cloud_router.get_final_transcription() as-is — the exact
function audio/consumers.py._finalize_segment already calls for real
session traffic (Groq primary, local faster-whisper fallback) — so
this endpoint gets the same reliability behavior for free and there
is no second STT code path to keep in sync. Nothing here imports from
or writes to conversation.models, audio.consumers, or anything else
in the live-session pipeline; this can be deleted entirely without
affecting copilot's original service.
"""

import io
import logging
import os
import wave

import numpy as np
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # matches Vontran's own cap (Accounts/transcription.py)


def _external_key_required(view_func):
    """
    Separate from accounts.auth.token_required on purpose (see module
    docstring) — checks a single shared-secret header instead of
    looking up a per-user AuthToken row. Comparison is intentionally
    NOT constant-time (no hmac.compare_digest): this key is a coarse
    service-to-service secret rotated by redeploying an env var, not a
    per-user credential, so the timing-attack surface this would
    protect against doesn't apply the way it would for a real user
    password/token lookup.
    """

    def wrapper(request, *args, **kwargs):
        configured = os.environ.get("EXTERNAL_TRANSCRIBE_API_KEY", "")
        if not configured:
            # Fails closed: an unset key means this endpoint is off,
            # not "any key accepted" — same "not configured yet" shape
            # as Vontran's own TRANSCRIPTION_API_KEY check.
            return JsonResponse({"error": "endpoint not configured"}, status=503)
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.startswith("Bearer ") or header[len("Bearer "):].strip() != configured:
            return JsonResponse({"error": "authentication required"}, status=401)
        return view_func(request, *args, **kwargs)

    return wrapper


def _decode_wav_to_float32_mono_16k(raw_bytes: bytes) -> np.ndarray:
    """
    Same decode contract Vontran's own Accounts/transcription.py
    _transcribe_local() was going to do locally before this got
    extradited here instead — 16-bit PCM WAV only, mono or stereo
    (downmixed), 16kHz required with no resample (see that module's
    comment on why: silently resampling wrong-rate audio produces
    quietly-bad transcripts rather than a loud, catchable failure).
    """
    with wave.open(io.BytesIO(raw_bytes), "rb") as wav:
        if wav.getsampwidth() != 2:
            raise ValueError("unsupported sample width (expected 16-bit PCM)")
        n_channels = wav.getnchannels()
        frame_rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())

    if frame_rate != 16000:
        raise ValueError(f"expected 16kHz audio, got {frame_rate}Hz")

    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0

    if n_channels == 2:
        audio = audio.reshape(-1, 2).mean(axis=1).astype(np.float32)
    elif n_channels != 1:
        raise ValueError(f"unsupported channel count: {n_channels}")

    return audio


@csrf_exempt
@require_http_methods(["POST"])
@_external_key_required
def transcribe_view(request):
    """
    POST multipart/form-data, field name 'audio' — a single WAV file,
    16-bit PCM, 16kHz, mono or stereo. Optional 'language' form field
    (ISO 639-1, e.g. "en"); defaults to "en" here rather than falling
    through to stt.engine's WHISPER_LANGUAGE env default, since the
    one caller of this endpoint (Vontran) added it specifically to
    force English and every other caller of get_final_transcription()
    in this codebase already resolves its own language before calling
    in — this is the one call site with no other source of truth for
    that choice, so the default lives here rather than silently
    depending on this project's own env config for a different
    project's behavior.

    Returns {"text": str, "confidence": float | None} — same shape
    stt.cloud_router.get_final_transcription() / stt.engine.py already
    return everywhere else in this codebase.
    """
    audio_file = request.FILES.get("audio")
    if not audio_file:
        return JsonResponse({"error": "no audio file provided"}, status=400)
    if audio_file.size > MAX_UPLOAD_BYTES:
        return JsonResponse({"error": "audio file too large"}, status=400)

    language = (request.POST.get("language") or "en").strip() or "en"

    try:
        audio = _decode_wav_to_float32_mono_16k(audio_file.read())
    except (wave.Error, ValueError) as exc:
        return JsonResponse({"error": f"could not read audio: {exc}"}, status=400)

    from stt.cloud_router import get_final_transcription  # deferred, same reason stt/engine.py defers faster_whisper

    try:
        result = get_final_transcription(audio, language=language)
    except Exception:
        logger.exception("external_transcribe.transcribe_view: get_final_transcription failed")
        return JsonResponse({"error": "transcription failed"}, status=502)

    return JsonResponse(result)
