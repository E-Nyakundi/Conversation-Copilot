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
        parser.add_argument(
            "--user-email",
            default=None,
            help="Which tenant's UserProfile to update (multi-tenancy retrofit, "
            "2026-08-30 — the knowledge base itself is still global/owner-only, "
            "see knowledge/models.py, but the persona summary it produces is "
            "written onto a specific per-user UserProfile row now). Defaults to "
            "the first superuser if omitted.",
        )

    def handle(self, *args, **options):
        from django.contrib.auth import get_user_model

        from conversation.models import UserProfile
        from knowledge.models import Document

        User = get_user_model()
        if options["user_email"]:
            try:
                target_user = User.objects.get(email__iexact=options["user_email"])
            except User.DoesNotExist:
                self.stderr.write(f"No user with email {options['user_email']!r}")
                return
        else:
            target_user = User.objects.filter(is_superuser=True).order_by("id").first()
            if target_user is None:
                self.stderr.write("No superuser found — pass --user-email explicitly.")
                return

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

        profile = UserProfile.get_for_user(target_user)
        profile.persona_summary = summary
        profile.persona_summary_updated_at = timezone.now()
        profile.save(update_fields=["persona_summary", "persona_summary_updated_at"])

        self.stdout.write(self.style.SUCCESS("Persona summary regenerated:"))
        self.stdout.write(summary)
