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

Phase 6 (2026-08-24): build_suggestion_prompt/build_code_prompt now
accept an optional knowledge_chunks param — retrieved passages from the
user's personal knowledge base (see knowledge/documents.py:
retrieve_context), formatted by the shared _build_knowledge_block helper
and injected ahead of live conversation context. This module still
doesn't do retrieval itself — the caller (audio/consumers.py) is
responsible for calling retrieve_knowledge_context() below (which wraps
knowledge.documents.retrieve_context with a safe-degrade fallback) and
passing the result in, same separation this docstring already described
for Phase 9's domain-mode templates. Phase 6 has now landed — this used
to say RAG "slots in later without a rewrite"; it has, and
retrieve_knowledge_context below is the single place that happens from.
"""

import logging
import os

logger = logging.getLogger(__name__)

SUGGESTION_SYSTEM_PROMPT = (
    "You are a real-time conversation copilot silently listening to a live "
    "conversation. A question has just been asked of the user. Suggest a "
    "direct answer they could say out loud RIGHT NOW, AS THEM — in first "
    "person, the way the user themselves would actually answer, not a "
    "third-person description of them. This is a spoken suggestion, not "
    "an essay.\n\n"
    "2026-08-31 (knowledge-base-as-voice): when background passages from "
    "the user's own documents are provided below, they are the single "
    "best source of truth for who this person is and what they'd say — "
    "prefer them over a generic, resume-shaped answer. If asked something "
    "like \"tell us about yourself,\" answer using those specifics (real "
    "roles, real projects, real experience from the documents), in first "
    "person, rather than a vague placeholder answer just because no exact "
    "matching sentence exists verbatim in the passages — reasonable, "
    "clearly-grounded inference from real background material is expected, "
    "not just quotation. If no relevant background material was provided "
    "for this particular question, answer as helpfully as you can without "
    "inventing specific facts (names, dates, employers, numbers) that "
    "were never given.\n\n"
    "Bug fix (2026-08-25, real on-device evidence): this prompt is also "
    "used for the spoken-alternative half of a code_and_prose answer (see "
    "ai/answer_format.py / audio/consumers.py) -- the companion piece "
    "shown alongside a real code suggestion so the user has something "
    "they can actually say out loud instead of reading code aloud. "
    "Without an explicit rule against it, this prompt was reaching for "
    "the exact vocabulary of a coding question (flag names, command "
    "syntax) and, on the worst observed case, writing an entire second "
    "fenced code block introduced with \"For example:\" -- defeating the "
    "whole point of a spoken alternative, since a person can't say a "
    "```bash ... ``` block out loud in an interview. This is a hard "
    "requirement, not a style preference: NEVER use a fenced code block "
    "(```), inline code formatting (`like this`), file paths, CLI flags, "
    "or verbatim command/function syntax -- even when the question is "
    "explicitly about code. Describe the approach and reasoning in your "
    "own plain words, the way you'd explain it to someone who can't see "
    "a screen: say \"I'd search for files older than 30 days and remove "
    "them safely\" -- not \"use `find` with `-mtime +30` and `-delete`\". "
    "If real code is genuinely needed, that's a separate suggestion the "
    "user already sees in parallel -- this one is spoken words only.\n\n"
    "Respond with ONLY the suggested answer itself: no preamble, no "
    "meta-commentary, no restating the question."
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
    "restating the question, no long preamble.\n\n"
    "2026-08-31: if background passages from the user's own documents are "
    "provided below (e.g. their real past projects or technical "
    "background), you may draw on them for any short explanatory prose "
    "around the code, in first person, as this specific person's own "
    "experience — but the code itself should simply be correct; don't "
    "distort a working solution just to reference the knowledge base."
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


def build_identity_context(name: str, role: str, bio: str) -> str:
    """
    Phase 5 (2026-08-24): turns a UserProfile's fields into a first-person
    identity instruction, prepended to whichever system prompt
    (SUGGESTION_SYSTEM_PROMPT or CODE_SYSTEM_PROMPT) is in use for this
    request. Caller (audio/consumers.py, via get_system_prompt below) is
    responsible for only calling this when UserProfile.is_populated() is
    true — an empty profile should mean zero prompt change from before
    this feature existed, not an instruction built from three empty
    strings.

    Kept as a standalone function returning plain text, not a method on
    UserProfile itself — this file already owns every other piece of
    prompt-construction logic (ANSWER_LEVELS, build_suggestion_prompt,
    build_code_prompt), so identity phrasing lives alongside it rather
    than splitting prompt-authoring across two files. conversation/
    models.py stays a pure data model with no prompt-engineering
    concerns baked in.

    Deliberately built from whichever of name/role/bio are actually
    non-empty, not a fixed three-blank template — a user who only filled
    in "role" shouldn't get "Your name is . " in the prompt.
    """
    parts = []
    if name.strip():
        parts.append(f"Your name is {name.strip()}.")
    if role.strip():
        parts.append(f"You are a {role.strip()}.")
    if bio.strip():
        parts.append(bio.strip())

    if not parts:
        return ""

    identity_block = " ".join(parts)
    return (
        f"{identity_block} When answering, respond AS this person, in "
        "first person — the suggested answer is what THEY would say about "
        "themselves, not a description of them. "
    )


def get_system_prompt(answer_format: str = "prose", user=None) -> str:
    """
    Selects CODE_SYSTEM_PROMPT or SUGGESTION_SYSTEM_PROMPT for
    answer_format, then prepends identity context from UserProfile if one
    is populated (see build_identity_context above and UserProfile's
    docstring in conversation/models.py for the full rationale).

    Added 2026-08-24 as the single place both ai/cloud_router.py and
    ai/local_llm.py select their system prompt from — previously each
    duplicated the same `if answer_format == "code": ... else: ...`
    selection independently; consolidating here means identity injection
    only had to be written once, and future prompt changes (Phase 9's
    domain modes, per that module's own module docstring) don't risk the
    two call sites drifting out of sync with each other.

    A profile-lookup failure (e.g. the migration hasn't run yet on an
    older deployment) degrades to no identity context rather than
    breaking suggestion generation entirely — the whole point of a
    silently-optional feature is that its absence shouldn't be able to
    take anything else down.

    Phase 6.5 (2026-08-24): also prepends UserProfile.persona_summary
    when present -- a distilled voice/personality characterization built
    from the whole knowledge base (see knowledge/persona.py and that
    field's docstring in conversation/models.py). This is deliberately
    separate from knowledge retrieval (_build_knowledge_block /
    retrieve_knowledge_context below): persona always shapes HOW the
    model answers, on every turn, regardless of whether that turn needs
    a specific fact; retrieval stays relevance-gated and only supplies
    WHAT the model answers with when a turn's question actually calls
    for it. Ordered after identity context (name/role/bio) and before
    the base system prompt -- same "small, bounded, always there"
    placement identity context already uses, and covered by the same
    lookup/exception so a persona lookup failure degrades no
    differently than an identity lookup failure would.

    Multi-tenancy retrofit (2026-08-30): UserProfile is no longer a
    global singleton (see conversation/models.py), so `user` (the
    session owner, an accounts.User) must be passed in to know whose
    profile to load. user=None degrades to no identity/persona context
    rather than falling back to some other user's profile.
    """
    base_prompt = CODE_SYSTEM_PROMPT if answer_format == "code" else SUGGESTION_SYSTEM_PROMPT

    if user is None:
        return base_prompt

    prefix = ""
    try:
        from conversation.models import UserProfile

        profile = UserProfile.get_for_user(user)
        if profile.is_populated():
            identity = build_identity_context(profile.name, profile.role, profile.bio)
            if identity:
                prefix += identity
        if profile.persona_summary.strip():
            prefix += (
                "How you should sound, based on the user's own notes and "
                f"documents: {profile.persona_summary.strip()} "
            )
    except Exception:
        logger.warning("Failed to load UserProfile for identity/persona context", exc_info=True)

    return prefix + base_prompt


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


def retrieve_knowledge_context(query: str, owner=None) -> list[str] | None:
    """
    Phase 6 (2026-08-24): the single place get_suggestion's callers
    would otherwise each have to import knowledge.documents themselves —
    consolidating here means audio/consumers.py doesn't need to know
    the knowledge app exists at all beyond calling this. Same
    safe-degrade posture as get_system_prompt's UserProfile lookup: a
    missing/uninstalled knowledge app (e.g. an older deployment that
    hasn't migrated yet) or any retrieval failure returns None rather
    than raising, and None is treated identically to "no knowledge base
    configured" by _build_knowledge_block below — never a reason to fail
    the whole suggestion.

    owner (2026-08-31, build plan §15): passed straight through to
    knowledge.documents.retrieve_context, which now requires it to
    return anything at all (a shared, unscoped FAISS index means
    "search with no owner" is no longer a safe default — see that
    function's docstring). Callers here MUST pass the session's actual
    owner; omitting it silently returns an empty knowledge base rather
    than erroring, so a caller that forgets this won't crash, but also
    won't get any grounding — check the call site if suggestions seem
    to be missing knowledge-base context that should be there.
    """
    try:
        from knowledge.documents import retrieve_context

        return retrieve_context(query, owner=owner)
    except Exception:
        logger.warning("Knowledge retrieval unavailable, degrading to no context", exc_info=True)
        return None


def build_code_prompt(
    question: str,
    context_turns: list[dict],
    level: str = DEFAULT_ANSWER_LEVEL,
    knowledge_chunks: list[str] | None = None,
) -> str:
    """
    Same shape as build_suggestion_prompt but paired with
    CODE_SYSTEM_PROMPT — level here loosely controls code
    thoroughness/explanation depth rather than sentence count, since
    "one short sentence" (quick's ANSWER_LEVELS instruction) doesn't
    mean anything for a code answer.

    knowledge_chunks (Phase 6, 2026-08-24): optional retrieved passages
    from the personal knowledge base — see build_suggestion_prompt's own
    docstring below for the full reasoning; identical handling here.
    """
    level = normalize_level(level)
    if context_turns:
        context_lines = "\n".join(f"{t['speaker']}: {t['text']}" for t in context_turns)
        context_block = f"Recent conversation:\n{context_lines}\n\n"
    else:
        context_block = ""

    knowledge_block = _build_knowledge_block(knowledge_chunks)

    depth_instruction = {
        "quick": "Keep it minimal — the core working solution only, no extra explanation.",
        "medium": "A working solution plus a brief note on any non-obvious choice.",
        "detailed": (
            "A working solution, briefly covering the main trade-off or "
            "edge case if there is one worth mentioning."
        ),
    }[level]

    return (
        f"{knowledge_block}{context_block}Coding question just asked: {question}\n\n"
        f"{depth_instruction}\n\nSuggested answer:"
    )


def _build_knowledge_block(knowledge_chunks: list[str] | None) -> str:
    """
    Phase 6: formats retrieved knowledge-base passages into a prompt
    block, or returns "" if there's nothing to inject — an empty/None
    knowledge_chunks list must produce byte-identical prompts to before
    Phase 6 existed, same "absence means silently unchanged" contract as
    build_identity_context above. Shared by both build_suggestion_prompt
    and build_code_prompt so the two don't drift on formatting.

    Placed first (before conversation context) so it reads as background
    reference material the model already "knows," with the live
    conversation as what's actually happening right now — the more
    time-sensitive/relevant-right-now content sits closer to the actual
    question.

    2026-08-24 (item 1 of the knowledge-base-as-personality bug): the
    original "use this if it's relevant, ignore it if it isn't" wording
    was too easy for the model to treat as optional/skippable filler,
    even though these passages were already selected by retrieve_context
    as relevant to this exact question (retrieval, not raw dumping —
    see that function's own scoring/min-score gate). Reworded to state
    plainly that this material was already selected as relevant, so the
    model doesn't need to re-decide whether to trust it.

    2026-08-31 (knowledge-base-as-voice, round 2): further reworded from
    "background you already know" (still readable as generic reference
    material a neutral assistant happens to have on hand) to explicitly
    framing these passages as the user's OWN documents about
    themselves — the source for what they'd actually say about their
    own background, not context for answering a question about a third
    party. Paired with the matching instruction now in
    SUGGESTION_SYSTEM_PROMPT/CODE_SYSTEM_PROMPT above.
    """
    if not knowledge_chunks:
        return ""
    passages = "\n\n".join(f"- {chunk}" for chunk in knowledge_chunks)
    return (
        "These passages are from the USER'S OWN documents about "
        "themselves (résumé, notes, profile, etc.), retrieved because "
        "they're relevant to the question just asked. They are real facts "
        f"about who this person is — answer as this person, using them:\n"
        f"{passages}\n\n"
    )


def build_suggestion_prompt(
    question: str,
    context_turns: list[dict],
    level: str = DEFAULT_ANSWER_LEVEL,
    knowledge_chunks: list[str] | None = None,
) -> str:
    """
    question: the extracted question text (Extraction.text where
    type == "question") to suggest an answer for.
    context_turns: recent [{"speaker": ..., "text": ...}] turns, oldest
    first, for grounding.
    knowledge_chunks (Phase 6, 2026-08-24): optional retrieved passages
    from the user's personal knowledge base (see
    knowledge/documents.py:retrieve_context) — plain strings, highest-
    relevance first. None/empty (the default, and what every caller
    passed before Phase 6 existed) produces the exact same prompt as
    before — this parameter is additive, not a required part of calling
    either builder. Callers (audio/consumers.py) are expected to call
    retrieve_knowledge_context() above and pass the result in; this
    module doesn't do retrieval itself, same separation of concerns as
    ai/prompts.py never calling ai/local_llm.py directly.
    level: one of ANSWER_LEVELS' keys (Phase 4) — invalid/missing values
    are normalized to DEFAULT_ANSWER_LEVEL rather than raising.
    """
    level = normalize_level(level)
    if context_turns:
        context_lines = "\n".join(f"{t['speaker']}: {t['text']}" for t in context_turns)
        context_block = f"Recent conversation:\n{context_lines}\n\n"
    else:
        context_block = ""

    knowledge_block = _build_knowledge_block(knowledge_chunks)

    instruction = ANSWER_LEVELS[level]["instruction"]
    return (
        f"{knowledge_block}{context_block}Question just asked: {question}\n\n"
        f"{instruction}\n\nSuggested answer:"
    )
