"""
Shared model warm-up logic for VAD, Whisper (STT), and the local LLM.

Used by two callers:
  - copilot/asgi.py — calls this at import time, i.e. inside the actual
    Daphne server process, before it starts accepting connections. This
    is the real fix.
  - management/commands/warmup_models.py — a thin CLI wrapper kept for
    manually checking model downloads/config (e.g. confirming a new
    WHISPER_MODEL_SIZE is cached) without booting the full server.

Why this used to be its own process in docker-compose.yml (`... &&
python manage.py warmup_models && daphne ...`): that process loaded the
models, printed "ready", and then exited — throwing all of that loading
away. Daphne then started fresh in a brand-new process with
stt.engine._engine / audio.vad._vad / ai.local_llm._llm all back to
None, so the *first real WebSocket connection* paid the full model-load
cost anyway. For Whisper specifically that also includes a live
huggingface.co API call to check the model revision (faster-whisper
does this on every fresh load unless HF_HUB_OFFLINE is set) — that
network round-trip was the ~5s "why does it take 5 seconds to say hi"
delay. Calling get_vad()/get_engine()/get_llm() from here instead means
there is exactly one load, in the same process that serves connections,
finished before Daphne binds its port.
"""

import os
import time


def run_warmup():
    print("=== Model warm-up starting ===", flush=True)

    # --- VAD ----------------------------------------------------------
    # Bundled inside the silero-vad pip package — no network call, but
    # loading it still touches torch/onnxruntime, so warm it up too and
    # surface any import/runtime errors early rather than on frame 1.
    t0 = time.monotonic()
    try:
        from audio.vad import get_vad

        get_vad()
        print(f"[warmup] VAD ready ({time.monotonic() - t0:.1f}s).", flush=True)
    except Exception as exc:
        print(f"[warmup] VAD failed to load: {exc}", flush=True)

    # --- Whisper (STT) --------------------------------------------------
    # This is the one that can actually hit the network — faster-whisper
    # downloads/verifies CTranslate2 weights against Hugging Face on
    # load. stt.engine already prints its own download-vs-cache-hit line
    # and timing; we just trigger it here so it happens once, now.
    try:
        from stt.engine import get_engine

        get_engine()
    except Exception as exc:
        print(f"[warmup] Whisper failed to load: {exc}", flush=True)

    # --- Local LLM (optional) -------------------------------------------
    # Requires a GGUF file you download yourself and point
    # LLM_MODEL_PATH at (see .env.example) — nothing to fetch from the
    # network here, so we just confirm it's configured and loadable.
    llm_path = os.environ.get("LLM_MODEL_PATH", "")
    if not llm_path:
        print(
            "[warmup] LLM_MODEL_PATH not set — skipping local LLM warm-up "
            "(suggestions will be silently disabled until you set it).",
            flush=True,
        )
    else:
        t0 = time.monotonic()
        try:
            from ai.local_llm import get_llm

            get_llm()
            print(f"[warmup] Local LLM ready ({time.monotonic() - t0:.1f}s).", flush=True)
        except Exception as exc:
            print(f"[warmup] Local LLM failed to load: {exc}", flush=True)

    print("=== Model warm-up complete ===", flush=True)
