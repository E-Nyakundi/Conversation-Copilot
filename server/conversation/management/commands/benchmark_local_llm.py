"""
Phase 3 — local LLM benchmarking (2026-08-15).

Ed's VM has no GPU (2 vCPU AMD EPYC — see build plan §6a), so "which
local model to use as the Groq fallback" isn't a question this sandbox
can answer: it depends on real latency/quality on that actual hardware,
which needs real GGUF files and a real machine to test on. What this
command *can* do is give a repeatable, fair harness for that comparison
once Ed has 2-3 candidate models downloaded — same spirit as
score_extraction.py for the rule-based extractor: a fixture-driven
harness, not a one-off ad-hoc test.

Does NOT touch the database or a live session. Loads each model in turn
(one at a time — these are big enough that loading two simultaneously on
a 2-vCPU box would just make both slower and muddy the timing), runs it
against a small fixed prompt set representative of real usage (a mix of
question lengths/styles, not just one easy example), and prints latency
+ full output per (model, prompt) pair so the results can be judged by
eye. Deliberately does NOT auto-pick a "winner" — response quality for a
live conversation copilot is a judgment call the build plan explicitly
left to Ed ("depends on your server's actual RAM/GPU"), not something
worth a fake numeric quality score.

Usage:
    python manage.py benchmark_local_llm --model /path/to/phi-3-mini-q4.gguf
    python manage.py benchmark_local_llm \\
        --model phi3=/path/to/phi-3-mini-q4.gguf \\
        --model qwen=/path/to/qwen2.5-3b-q4.gguf
    python manage.py benchmark_local_llm --model /path/to/model.gguf --level quick
"""

import time

from django.core.management.base import BaseCommand, CommandError

from ai.prompts import ANSWER_LEVELS, DEFAULT_ANSWER_LEVEL

# Small, deliberately mixed prompt set — not just one easy "what time is
# it?" case. Mirrors the kinds of asks seen in real sessions so far this
# build (see build plan's Phase 2/3 entries): a short factual question,
# an imperative coding ask (exercises the same subtype="imperative" path
# as the context-window fix), and a longer multi-constraint system-design
# question (exercises whether the model can actually use a fuller
# context window, not just answer in a vacuum).
BENCHMARK_CASES = [
    {
        "question": "What's the trade-off between REST and GraphQL for a mobile client?",
        "context_turns": [],
    },
    {
        "question": "Write a function in Python 3 that reverses a linked list in place.",
        "context_turns": [
            {"speaker": "B", "text": "Let's do a quick coding warm-up before the real interview."},
        ],
    },
    {
        "question": (
            "Given all that — the offline-first requirement, the two-user "
            "conflict case, and the need to keep edits feeling instant — "
            "walk through how you'd actually implement the sync layer."
        ),
        "context_turns": [
            {"speaker": "B", "text": "We're building an offline-first collaborative editor."},
            {"speaker": "A", "text": "Okay, so local-first with eventual sync."},
            {"speaker": "B", "text": "Right. Two users can edit the same block while both offline."},
            {"speaker": "A", "text": "Got it."},
            {"speaker": "B", "text": "And it has to feel snappy — no visible lag on keystrokes."},
        ],
    },
]


class Command(BaseCommand):
    help = "Benchmark one or more local GGUF models for suggestion latency, one prompt set, side by side."

    def add_arguments(self, parser):
        parser.add_argument(
            "--model",
            action="append",
            required=True,
            help=(
                "GGUF file path, or name=path to label it in output "
                "(e.g. phi3=/models/phi-3-mini-q4.gguf). Repeatable."
            ),
        )
        parser.add_argument(
            "--level",
            choices=list(ANSWER_LEVELS),
            default=DEFAULT_ANSWER_LEVEL,
            help=f"Answer level to benchmark at (default: {DEFAULT_ANSWER_LEVEL}).",
        )

    def handle(self, *args, **options):
        # Deferred import — see ai/local_llm.py's own module docstring for
        # why: importing this command shouldn't require llama-cpp-python
        # to be installed just to run `manage.py help`.
        try:
            from llama_cpp import Llama
        except ImportError as exc:
            raise CommandError(
                "llama-cpp-python isn't installed. It's in requirements.txt "
                "as of 2026-08-15 (pointed at the prebuilt CPU wheel index "
                "to avoid the old source-build timeout) — run `pip install "
                "-r requirements.txt` first."
            ) from exc

        level = options["level"]
        model_specs = self._parse_model_specs(options["model"])

        for label, model_path in model_specs:
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n=== {label} ({model_path}) ==="))

            load_started = time.monotonic()
            try:
                llm = Llama(model_path=model_path, n_ctx=4096, n_gpu_layers=0, verbose=False)
            except Exception as exc:  # noqa: BLE001 — surfacing any load failure as-is is more useful here than swallowing it
                self.stderr.write(self.style.ERROR(f"  Failed to load: {exc}"))
                continue
            load_ms = round((time.monotonic() - load_started) * 1000)
            self.stdout.write(f"  Model load: {load_ms}ms")

            for case in BENCHMARK_CASES:
                self._run_case(llm, case, level)

            # Explicit teardown before the next model — these can hold
            # several GB; don't rely on GC timing to free it before the
            # next Llama(...) call on a memory-constrained VM.
            del llm

    def _parse_model_specs(self, raw_specs):
        specs = []
        for i, raw in enumerate(raw_specs, start=1):
            if "=" in raw:
                label, path = raw.split("=", 1)
            else:
                label, path = f"model{i}", raw
            specs.append((label, path))
        return specs

    def _run_case(self, llm, case, level):
        from ai.prompts import SUGGESTION_SYSTEM_PROMPT, build_suggestion_prompt, get_max_tokens

        question = case["question"]
        user_prompt = build_suggestion_prompt(question, case["context_turns"], level)
        max_tokens = get_max_tokens(level)

        self.stdout.write(f"\n  Q: {question[:80]}{'...' if len(question) > 80 else ''}")

        started = time.monotonic()
        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": SUGGESTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.4,
        )
        latency_ms = round((time.monotonic() - started) * 1000)

        text = response["choices"][0]["message"]["content"].strip()
        self.stdout.write(f"  [{latency_ms}ms] {text}")
