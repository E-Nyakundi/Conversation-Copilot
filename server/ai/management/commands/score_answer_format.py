"""
Phase B (2026-08-23) checkpoint tooling for ai/answer_format.py, mirroring
conversation/management/commands/score_extraction.py's structure and
purpose for the sibling classifier.

Unlike score_extraction (which prints results for manual eyeballing since
extraction has no single "correct" answer per turn), answer_format's
fixtures carry an explicit expected label, so this command can actually
report pass/fail counts — the classifier decides among exactly three
labels, and Ed's real-session reports gave concrete "this was
misclassified" examples to lock in as regression cases. It still prints
every case (not just failures) so the fixture set itself stays legible
for skimming.

Usage:
    python manage.py score_answer_format
    python manage.py score_answer_format --verbose
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand

from ai import answer_format

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "answer_format_samples.json"


class Command(BaseCommand):
    help = "Score ai/answer_format.py's classifier against ai/fixtures/answer_format_samples.json"

    def add_arguments(self, parser):
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Also print each case's note, not just text/expected/got.",
        )

    def handle(self, *args, **options):
        verbose = options["verbose"]

        with open(FIXTURE_PATH) as f:
            data = json.load(f)

        cases = data["cases"]
        passed = 0

        for case in cases:
            text = case["text"]
            expected = case["expected"]
            got = answer_format.classify(text)
            ok = got == expected

            if ok:
                passed += 1
                marker = self.style.SUCCESS("OK  ")
            else:
                marker = self.style.ERROR("FAIL")

            self.stdout.write(f'{marker} expected={expected:14s} got={got:14s} | "{text}"')
            if verbose and case.get("note"):
                self.stdout.write(f"      note: {case['note']}")

        total = len(cases)
        summary_style = self.style.MIGRATE_HEADING if passed == total else self.style.ERROR
        self.stdout.write(summary_style(f"\n{passed}/{total} passed."))

        if passed < total:
            self.stdout.write(self.style.ERROR(
                "One or more fixtures failed — this is a real regression, "
                "not something to eyeball away. Fix the classifier or, if "
                "the expected label itself was wrong, fix the fixture."
            ))
