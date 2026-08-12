"""
Thin CLI wrapper around audio.warmup.run_warmup() — lets you eagerly load
VAD, Whisper (STT), and the local LLM by hand:

    python manage.py warmup_models

The real warm-up now happens automatically inside the Daphne process
itself (see copilot/asgi.py), so this is no longer part of the normal
startup path (docker-compose.yml just runs migrate + daphne). Keep this
around for manually checking model downloads/config — e.g. confirming a
new WHISPER_MODEL_SIZE is cached, or that LLM_MODEL_PATH resolves —
without booting the full server.
"""

from django.core.management.base import BaseCommand

from audio.warmup import run_warmup


class Command(BaseCommand):
    help = "Eagerly load VAD, Whisper (STT), and local LLM models with visible progress."

    def handle(self, *args, **options):
        run_warmup()
