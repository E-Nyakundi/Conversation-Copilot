"""
Phase 2 go/no-go checkpoint tooling.

Runs conversation/intent.py (or, as of 2026-08-16, conversation/
llm_extract.py) against the bundled sample transcripts
(conversation/fixtures/sample_transcripts.json) and prints every turn next
to whatever got extracted from it, so you can manually score precision/
recall by eye per the build plan ("score extraction accuracy manually
against real/realistic transcript samples").

This does NOT touch the database or a live session — it's a pure offline
harness for judging the extractor, or comparing the LLM-based extractor
against this same fixture set on equal footing (the exact comparison this
docstring called out as future work, before llm_extract.py existed).

Usage:
    python manage.py score_extraction
    python manage.py score_extraction --transcript sales_call
    python manage.py score_extraction --type question
    python manage.py score_extraction --mode llm
    python manage.py score_extraction --mode llm --transcript interview_practice
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand

from conversation import intent

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "sample_transcripts.json"

_TYPE_LABELS = {
    intent.QUESTION: "QUESTION",
    intent.DECISION: "DECISION",
    intent.REQUEST: "REQUEST",
    intent.OBJECTION: "OBJECTION",
    intent.ACTION_ITEM: "ACTION",
    intent.TOPIC_CHANGE: "TOPIC_CHANGE",
}


class Command(BaseCommand):
    help = "Run the rule-based extractor against sample transcripts for manual accuracy scoring."

    def add_arguments(self, parser):
        parser.add_argument(
            "--transcript",
            help="Only run one transcript by key (see sample_transcripts.json). Default: all.",
        )
        parser.add_argument(
            "--type",
            choices=list(intent.ALL_TYPES),
            help="Only show extractions of this type.",
        )
        parser.add_argument(
            "--mode",
            choices=["rule_based", "llm"],
            default="rule_based",
            help=(
                "Which extractor to run: rule_based (intent.py, default) "
                "or llm (llm_extract.py, requires GROQ_API_KEY — a real "
                "API call per turn, will be slower and cost tokens)."
            ),
        )

    def handle(self, *args, **options):
        transcripts = json.loads(FIXTURE_PATH.read_text())

        only_transcript = options.get("transcript")
        only_type = options.get("type")
        mode = options.get("mode", "rule_based")

        if mode == "llm":
            from conversation import llm_extract
            extract_fn = llm_extract.extract
            self.stdout.write(self.style.WARNING(
                "Running in --mode llm: one real Groq API call per turn. "
                "Requires GROQ_API_KEY set."
            ))
        else:
            extract_fn = intent.extract

        if only_transcript:
            if only_transcript not in transcripts:
                self.stderr.write(f"Unknown transcript '{only_transcript}'. Options: {list(transcripts)}")
                return
            transcripts = {only_transcript: transcripts[only_transcript]}

        total_items = 0
        total_turns = 0

        for name, lines in transcripts.items():
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n=== {name} ==="))
            previous_text = None

            for line in lines:
                # Fixture lines look like "A: some text" — strip the speaker
                # label since intent.py operates on turn text only.
                text = line.split(": ", 1)[1] if ": " in line else line
                total_turns += 1

                # In --mode llm, a single turn's API call failing (rate
                # limit, timeout, malformed JSON) shouldn't abort scoring
                # the rest of the fixture set — print and treat as zero
                # items for this turn, same "additive, not a blocker"
                # posture as the live consumers.py fallback.
                try:
                    items = extract_fn(text, previous_text)
                except Exception as e:
                    self.stderr.write(self.style.ERROR(f'  "{text}" -> extraction failed: {e!r}'))
                    items = []

                if only_type:
                    items = [i for i in items if i.type == only_type]

                self.stdout.write(f'\n  "{text}"')
                if not items:
                    self.stdout.write(self.style.WARNING("    (nothing extracted)"))
                for item in items:
                    total_items += 1
                    label = _TYPE_LABELS.get(item.type, item.type.upper())
                    self.stdout.write(
                        self.style.SUCCESS(f"    -> {label}") + f" (confidence {item.confidence}): {item.text}"
                    )

                previous_text = text

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"\n{total_items} item(s) extracted across {total_turns} turn(s). "
                "Read through the above and judge false positives/negatives by eye — "
                "that's the Phase 2 checkpoint, not a number this command can produce for you."
            )
        )
