"""
Loads every AI model the pipeline depends on, once, at container startup —
with clear, visible progress in `docker logs` — instead of letting them
load lazily and silently on the first WebSocket audio frame.

Run manually:
    python manage.py warmup_models

In docker-compose.yml this runs automatically before Daphne starts, so
`docker compose up` output tells you plainly whether a model is
downloading (first run, or after the hf_cache volume was wiped) or
loading from cache (fast, every run after that) — rather than the mic
test just appearing to hang or crash with zero feedback.
"""

import os
import time

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Eagerly load VAD, Whisper (STT), and local LLM models with visible progress."

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("=== Model warm-up starting ==="))

        # --- VAD ------------------------------------------------------
        # Bundled inside the silero-vad pip package — no network call,
        # but loading it still touches torch/onnxruntime, so warm it up
        # too and surface any import/runtime errors early.
        t0 = time.monotonic()
        try:
            from audio.vad import get_vad

            get_vad()
            self.stdout.write(
                self.style.SUCCESS(f"[warmup] VAD ready ({time.monotonic() - t0:.1f}s).")
            )
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"[warmup] VAD failed to load: {exc}"))

        # --- Whisper (STT) ----------------------------------------------
        # This is the one that can actually hit the network — faster-whisper
        # downloads CTranslate2 weights from Hugging Face on first use.
        # stt.engine already prints its own download-vs-cache-hit line and
        # timing; we just trigger it here so it happens now, not on your
        # first mic test.
        try:
            from stt.engine import get_engine

            get_engine()
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"[warmup] Whisper failed to load: {exc}"))

        # --- Local LLM (Phase 3, optional) -------------------------------
        # Requires a GGUF file you download yourself and point
        # LLM_MODEL_PATH at (see .env.example) — nothing to fetch from the
        # network here, so we just confirm it's configured and loadable.
        llm_path = os.environ.get("LLM_MODEL_PATH", "")
        if not llm_path:
            self.stdout.write(
                self.style.WARNING(
                    "[warmup] LLM_MODEL_PATH not set — skipping local LLM warm-up "
                    "(suggestions will be silently disabled until you set it)."
                )
            )
        else:
            t0 = time.monotonic()
            try:
                from ai.local_llm import get_llm

                get_llm()
                self.stdout.write(
                    self.style.SUCCESS(f"[warmup] Local LLM ready ({time.monotonic() - t0:.1f}s).")
                )
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"[warmup] Local LLM failed to load: {exc}"))

        self.stdout.write(self.style.MIGRATE_HEADING("=== Model warm-up complete ==="))
