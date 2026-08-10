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

from ai.prompts import SUGGESTION_SYSTEM_PROMPT, build_suggestion_prompt

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


def _suggest_via_groq(question: str, context_turns: list[dict]) -> dict:
    """
    Raises on any Groq-side failure — get_suggestion() decides whether to
    fall back. Returns {"text": str, "model": str} on success, the same
    shape ai.local_llm.LocalLLM.suggest_answer() returns.
    """
    from groq import APIConnectionError, APIError, APITimeoutError, RateLimitError

    client = _get_groq_client()
    model = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
    max_tokens = int(os.environ.get("LLM_MAX_TOKENS", "200"))
    temperature = float(os.environ.get("LLM_TEMPERATURE", "0.4"))
    user_prompt = build_suggestion_prompt(question, context_turns)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SUGGESTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
    except (APIConnectionError, APITimeoutError, RateLimitError, APIError) as e:
        raise RuntimeError(f"Groq request failed: {e}") from e

    choice = response.choices[0].message.content if response.choices else None
    if not choice or not choice.strip():
        raise RuntimeError("Groq returned an empty response")

    return {"text": choice.strip(), "model": f"groq:{model}"}


def get_suggestion(question: str, context_turns: list[dict]) -> dict:
    """
    Public entry point — same signature and return shape as
    ai.local_llm.LocalLLM.suggest_answer(), so audio/consumers.py can call
    this instead with no other changes needed on the caller's side.

    question: extracted question text to answer.
    context_turns: recent [{"speaker": ..., "text": ...}] turns, oldest
    first — passed straight through to whichever path handles the call.
    """
    if os.environ.get("GROQ_API_KEY"):
        try:
            return _suggest_via_groq(question, context_turns)
        except Exception:
            logger.warning(
                "Groq suggestion failed, falling back to local model",
                exc_info=True,
            )
    else:
        logger.debug("GROQ_API_KEY not set, using local model directly")

    from ai.local_llm import get_llm

    return get_llm().suggest_answer(question, context_turns)
