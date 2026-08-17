"""
Phase 3: prompt construction for suggestion generation.

Kept separate from ai/local_llm.py so Phase 9's domain-mode prompt
templates (interview practice, sales, negotiation, etc.) can plug in here
later — swapping SUGGESTION_SYSTEM_PROMPT per-session-mode — without
touching the model wrapper itself. For now there's a single general-
purpose prompt; mode-awareness is explicitly out of scope until Phase 9.

Phase 4: answer-level switching (Quick / Medium / Detailed) lives here
too, as ANSWER_LEVELS — a per-level (instruction, max_tokens) pair, kept
next to the prompt builder that consumes it rather than scattered across
ai/local_llm.py and ai/cloud_router.py, since both callers need the exact
same level->instruction and level->token-budget mapping and drift between
them would be a silent bug.
"""

import os

SUGGESTION_SYSTEM_PROMPT = (
    "You are a real-time conversation copilot silently listening to a live "
    "conversation. A question has just been asked of the user. Suggest a "
    "direct answer they could say out loud RIGHT NOW. This is a spoken "
    "suggestion, not an essay. Respond with ONLY the suggested answer "
    "itself: no preamble, no meta-commentary, no restating the question."
)

# Phase 5 (2026-08-16): a separate prompt for code-shaped asks (see
# ai/answer_format.py) — SUGGESTION_SYSTEM_PROMPT above is explicitly
# tuned for spoken brevity ("not an essay") which was actively fighting
# real code answers: a coding-interview question routed through it came
# back describing what code *would* do rather than writing it (confirmed
# against Ed's real session screenshots, 2026-08-16). This prompt asks
# for the opposite: an actual working answer in a fenced code block,
# with only as much prose as needed to explain non-obvious choices.
CODE_SYSTEM_PROMPT = (
    "You are a real-time conversation copilot silently listening to a live "
    "technical conversation (e.g. a coding interview). A coding question "
    "has just been asked of the user. Respond with a real, working answer: "
    "actual code in a fenced code block (```language ... ```), not a "
    "description of code. Keep it complete enough to actually work but no "
    "more — skip boilerplate/imports that aren't the point of the "
    "question. Add at most 1-2 short sentences of explanation for a "
    "genuinely non-obvious choice (e.g. why a particular data structure), "
    "before or after the code block, not instead of the code. No "
    "restating the question, no long preamble."
)

DEFAULT_ANSWER_LEVEL = "medium"

# max_tokens is a generation budget, not a target length — the model can
# (and often will) land short of it. Overridable per-level via env (see
# get_max_tokens) for tuning without a code change once real usage shows
# whether these defaults are too generous/stingy on your actual hardware.
#
# 2026-08-17: bumped up from the original 60/150/350 (tuned for the old
# non-reasoning llama-3.1-8b-instant) to give gpt-oss-20b's hidden
# reasoning tokens headroom on top of the visible answer — see
# ai/cloud_router.py's reasoning_effort comment for the full incident.
# This is a backstop alongside reasoning_effort=low, not the primary fix.
ANSWER_LEVELS = {
    "quick": {
        "label": "Quick",
        "instruction": (
            "Answer in one short sentence — the fewest words that fully "
            "answer it, sayable out loud in under 3 seconds."
        ),
        "max_tokens": 150,
    },
    "medium": {
        "label": "Medium",
        "instruction": (
            "Answer in two to three concise sentences — enough to be "
            "useful without rambling."
        ),
        "max_tokens": 300,
    },
    "detailed": {
        "label": "Detailed",
        "instruction": (
            "Answer more fully — a short paragraph is fine if it adds "
            "real value, but stay focused and avoid padding."
        ),
        "max_tokens": 600,
    },
}


def normalize_level(level):
    """Any unrecognized/missing level quietly falls back to the default
    rather than raising — a malformed client message shouldn't be able to
    break suggestion generation."""
    return level if level in ANSWER_LEVELS else DEFAULT_ANSWER_LEVEL


def get_max_tokens(level):
    """
    Per-level token budget, overridable via env as
    LLM_MAX_TOKENS_<LEVEL> (e.g. LLM_MAX_TOKENS_QUICK). Falls back to
    ANSWER_LEVELS' hardcoded default if the env var isn't set.
    """
    level = normalize_level(level)
    env_val = os.environ.get(f"LLM_MAX_TOKENS_{level.upper()}")
    if env_val:
        return int(env_val)
    return ANSWER_LEVELS[level]["max_tokens"]


# Phase 5 (2026-08-16): code answers need a much bigger budget than any
# ANSWER_LEVELS entry — none of quick/medium/detailed fit an actual
# function body, let alone the surrounding fenced block and a line or
# two of explanation. Kept as flat level->tokens map (not a multiplier
# on ANSWER_LEVELS) so each can be tuned independently once real usage
# shows whether these are too generous/stingy, same reasoning
# ANSWER_LEVELS' own docstring gives for its numbers.
#
# 2026-08-17: bumped from 250/500/900 — same reasoning-model headroom
# fix as ANSWER_LEVELS above; code prompts were hitting empty responses
# more than prose ones since they're longer/harder, giving gpt-oss-20b's
# hidden reasoning more room to consume the whole budget.
CODE_MAX_TOKENS = {
    "quick": 500,
    "medium": 900,
    "detailed": 1500,
}


def get_code_max_tokens(level):
    """Same override convention as get_max_tokens, via LLM_CODE_MAX_TOKENS_<LEVEL>."""
    level = normalize_level(level)
    env_val = os.environ.get(f"LLM_CODE_MAX_TOKENS_{level.upper()}")
    if env_val:
        return int(env_val)
    return CODE_MAX_TOKENS[level]


def build_code_prompt(question: str, context_turns: list[dict], level: str = DEFAULT_ANSWER_LEVEL) -> str:
    """
    Same shape as build_suggestion_prompt but paired with
    CODE_SYSTEM_PROMPT — level here loosely controls code
    thoroughness/explanation depth rather than sentence count, since
    "one short sentence" (quick's ANSWER_LEVELS instruction) doesn't
    mean anything for a code answer.
    """
    level = normalize_level(level)
    if context_turns:
        context_lines = "\n".join(f"{t['speaker']}: {t['text']}" for t in context_turns)
        context_block = f"Recent conversation:\n{context_lines}\n\n"
    else:
        context_block = ""

    depth_instruction = {
        "quick": "Keep it minimal — the core working solution only, no extra explanation.",
        "medium": "A working solution plus a brief note on any non-obvious choice.",
        "detailed": (
            "A working solution, briefly covering the main trade-off or "
            "edge case if there is one worth mentioning."
        ),
    }[level]

    return (
        f"{context_block}Coding question just asked: {question}\n\n"
        f"{depth_instruction}\n\nSuggested answer:"
    )


def build_suggestion_prompt(question: str, context_turns: list[dict], level: str = DEFAULT_ANSWER_LEVEL) -> str:
    """
    question: the extracted question text (Extraction.text where
    type == "question") to suggest an answer for.
    context_turns: recent [{"speaker": ..., "text": ...}] turns, oldest
    first, for grounding. Phase 3 has no knowledge base yet (that's Phase
    6) — this is conversation context only, not retrieved facts, so the
    model is working from what it already knows plus what's been said out
    loud in this session.
    level: one of ANSWER_LEVELS' keys (Phase 4) — invalid/missing values
    are normalized to DEFAULT_ANSWER_LEVEL rather than raising.
    """
    level = normalize_level(level)
    if context_turns:
        context_lines = "\n".join(f"{t['speaker']}: {t['text']}" for t in context_turns)
        context_block = f"Recent conversation:\n{context_lines}\n\n"
    else:
        context_block = ""

    instruction = ANSWER_LEVELS[level]["instruction"]
    return f"{context_block}Question just asked: {question}\n\n{instruction}\n\nSuggested answer:"
