"""
Phase 6.5 (2026-08-24): rebuilds UserProfile.persona_summary from the
current knowledge base via knowledge/persona.py:build_persona_summary().
Mirrors reindex_knowledge's shape (a manual, explicit rebuild command,
not something that runs automatically on every ingest) — see that
command's and persona.py's docstrings for why an automatic per-upload
rebuild would be wasteful.

Usage:
    python manage.py regenerate_persona
    python manage.py regenerate_persona --yes   # skip the confirmation prompt
"""

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Rebuild UserProfile.persona_summary from the current knowledge base."

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip the confirmation prompt (for non-interactive use, e.g. a deploy script).",
        )

    def handle(self, *args, **options):
        from conversation.models import UserProfile
        from knowledge.models import Document

        doc_count = Document.objects.count()
        if doc_count == 0:
            self.stdout.write("No documents in the knowledge base — nothing to summarize.")
            return

        if not options["yes"]:
            confirm = input(
                f"This will call Groq to summarize {doc_count} document(s) into a new "
                f"persona summary, replacing the current one. Continue? [y/N] "
            )
            if confirm.strip().lower() != "y":
                self.stdout.write("Aborted.")
                return

        from knowledge.persona import build_persona_summary

        summary = build_persona_summary()
        if not summary:
            self.stdout.write(
                self.style.WARNING(
                    "Persona summary generation returned nothing (see logs) — "
                    "UserProfile.persona_summary left unchanged."
                )
            )
            return

        profile = UserProfile.get_solo()
        profile.persona_summary = summary
        profile.persona_summary_updated_at = timezone.now()
        profile.save(update_fields=["persona_summary", "persona_summary_updated_at"])

        self.stdout.write(self.style.SUCCESS("Persona summary regenerated:"))
        self.stdout.write(summary)
