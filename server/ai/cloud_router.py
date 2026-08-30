"""
Phase 3 (v4): hybrid suggestion router — Groq (cloud, primary) with the
local LLM (ai/local_llm.py) as fallback on network failure/timeout/rate
limit, per the build plan's "Groq primary, local 3B fallback" decision.

Mirrors the lazy-singleton + deferred-import pattern used throughout this
app (stt/engine.py, audio/vad.py, ai/local_llm.py) so importing this
module doesn't require the `groq` package installed, or GROQ_API_KEY
configured, just to run `manage.py check`, admin, REST endpoints, etc.

get_suggestion() order of operations:
1. If GROQ_API_KEY is set, try Groq first (fast, free tier, LPU hardware).
2. On any Groq-specific failure (missing/bad key, connection/timeout
   error, rate limit, empty/malformed response) — log it and fall back
   to the local model via ai.local_llm.get_llm().
3. If GROQ_API_KEY isn't set at all, skip straight to the local model —
   no point attempting a call that can't succeed.
4. Whatever the local path raises (e.g. FileNotFoundError if
   LLM_MODEL_PATH isn't configured either) propagates to the caller —
   audio/consumers.py._generate_suggestion already treats a raised
   exception here as "no suggestion this time", not a fatal error; see
   that module's docstring.

Deliberately NOT a bare `except Exception` around the Groq call — only
the categories of failure that are genuinely Groq-specific (auth,
connection, timeout, rate limit, malformed/empty response) trigger the
fallback. A bug in prompt construction, for instance, would fail
identically on both paths and should surface rather than be silently
masked as "Groq is down."
"""

import logging
import os
import threading

from ai.prompts import (
    DEFAULT_ANSWER_LEVEL,
    build_code_prompt,
    build_suggestion_prompt,
    get_code_max_tokens,
    get_max_tokens,
    get_system_prompt,
)

logger = logging.getLogger(__name__)

_groq_client = None
_groq_lock = threading.Lock()


def _get_groq_client():
    """Thread-safe lazy singleton, same shape as ai.local_llm.get_llm()."""
    global _groq_client
    if _groq_client is None:
        with _groq_lock:
            if _groq_client is None:
                from groq import Groq  # deferred import — see module docstring

                api_key = os.environ.get("GROQ_API_KEY", "")
                if not api_key:
                    raise RuntimeError("GROQ_API_KEY is not set")
                _groq_client = Groq(api_key=api_key)
    return _groq_client


def _suggest_via_groq(
    question: str,
    context_turns: list[dict],
    level: str = DEFAULT_ANSWER_LEVEL,
    answer_format: str = "prose",
    knowledge_chunks: list[str] | None = None,
    user=None,
) -> dict:
    """
    Raises on any Groq-side failure — get_suggestion() decides whether to
    fall back. Returns {"text": str, "model": str} on success, the same
    shape ai.local_llm.LocalLLM.suggest_answer() returns.

    answer_format (2026-08-16): "prose" (default, unchanged behavior) or
    "code" — selects the code-tuned system prompt/build_code_prompt and a
    much larger token budget instead of the prose prompt's spoken-brevity
    path (see ai/prompts.py:get_system_prompt). See ai/answer_format.py
    for how a question gets routed to one or the other.

    knowledge_chunks (Phase 6, 2026-08-24): optional retrieved passages
    from the personal knowledge base, passed straight through to
    ai/prompts.py's builders — see that module's docstring. None/empty
    (the default) produces the exact same prompt as before Phase 6.
    """
    from groq import APIConnectionError, APIError, APITimeoutError, RateLimitError

    client = _get_groq_client()
    # 2026-08-17: llama-3.1-8b-instant was deprecated by Groq (every call
    # was 404ing in prod — see console.groq.com/docs/deprecations). Groq's
    # own migration guidance recommends openai/gpt-oss-20b as the
    # replacement. Still fully overridable via GROQ_MODEL.
    model = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")
    temperature = float(os.environ.get("LLM_TEMPERATURE", "0.4"))

    if answer_format == "code":
        max_tokens = get_code_max_tokens(level)
        user_prompt = build_code_prompt(question, context_turns, level, knowledge_chunks=knowledge_chunks)
    else:
        max_tokens = get_max_tokens(level)
        user_prompt = build_suggestion_prompt(question, context_turns, level, knowledge_chunks=knowledge_chunks)
    # 2026-08-24: get_system_prompt() prepends first-person identity
    # context from UserProfile when one is populated — see that
    # function's docstring in ai/prompts.py. Byte-identical to the old
    # CODE_SYSTEM_PROMPT/SUGGESTION_SYSTEM_PROMPT selection when no
    # profile exists yet.
    system_prompt = get_system_prompt(answer_format, user=user)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            # 2026-08-17: gpt-oss-20b is a reasoning model — it spends part
            # of max_tokens on hidden chain-of-thought before writing any
            # visible content. With max_tokens budgets tuned for the old
            # non-reasoning llama-3.1-8b-instant (60/150/350 prose,
            # 250/500/900 code), reasoning was intermittently eating the
            # entire budget on harder/longer prompts, leaving
            # choices[0].message.content empty — real prod evidence: ~6 of
            # 15 suggestion attempts in one session hit "Groq returned an
            # empty response" specifically on system-design/code prompts,
            # never on short ones like "What's your name?". reasoning_effort
            # (Groq-specific, only supported by gpt-oss-20b/120b: low/medium/
            # high, default medium) directly reduces reasoning-token spend
            # rather than just giving it more room to still consume — kept
            # as the primary fix, with the max_tokens bumps below as a
            # backstop in case low-effort reasoning still eats a meaningful
            # chunk on the hardest prompts.
            reasoning_effort=os.environ.get("GROQ_REASONING_EFFORT", "low"),
        )
    except (APIConnectionError, APITimeoutError, RateLimitError, APIError) as e:
        raise RuntimeError(f"Groq request failed: {e}") from e

    choice = response.choices[0].message.content if response.choices else None
    if not choice or not choice.strip():
        raise RuntimeError("Groq returned an empty response")

    return {"text": choice.strip(), "model": f"groq:{model}"}


def get_suggestion(
    question: str,
    context_turns: list[dict],
    level: str = DEFAULT_ANSWER_LEVEL,
    answer_format: str = "prose",
    knowledge_chunks: list[str] | None = None,
    user=None,
) -> dict:
    """
    Public entry point — same signature and return shape as
    ai.local_llm.LocalLLM.suggest_answer(), so audio/consumers.py can call
    this instead with no other changes needed on the caller's side.

    question: extracted question text to answer.
    context_turns: recent [{"speaker": ..., "text": ...}] turns, oldest
    first — passed straight through to whichever path handles the call.
    level: Phase 4 answer-level (quick/medium/detailed) — see
    ai/prompts.py:ANSWER_LEVELS. Passed through to both the Groq and
    local paths so a level switch behaves identically regardless of
    which one actually serves the request.
    answer_format: "prose" (default) or "code" — see ai/answer_format.py.
    Passed through to both the Groq and local paths identically.
    knowledge_chunks (Phase 6, 2026-08-24): optional retrieved passages
    from the personal knowledge base — passed through to both paths
    identically, so a Groq failure falling back to local generation
    doesn't silently lose knowledge-base grounding, same "don't let the
    fallback regress" rule already applied to identity context.
    """
    if os.environ.get("GROQ_API_KEY"):
        try:
            return _suggest_via_groq(question, context_turns, level, answer_format, knowledge_chunks, user=user)
        except Exception:
            logger.warning(
                "Groq suggestion failed, falling back to local model",
                exc_info=True,
            )
    else:
        logger.debug("GROQ_API_KEY not set, using local model directly")

    from ai.local_llm import get_llm

    return get_llm().suggest_answer(question, context_turns, level, answer_format, knowledge_chunks, user=user)
