"""
Phase 3: prompt construction for suggestion generation.

Kept separate from ai/local_llm.py so Phase 9's domain-mode prompt
templates (interview practice, sales, negotiation, etc.) can plug in here
later — swapping SUGGESTION_SYSTEM_PROMPT per-session-mode — without
touching the model wrapper itself. For now there's a single general-
purpose prompt; mode-awareness is explicitly out of scope until Phase 9.
"""

SUGGESTION_SYSTEM_PROMPT = (
    "You are a real-time conversation copilot silently listening to a live "
    "conversation. A question has just been asked of the user. Suggest a "
    "short, direct answer they could say out loud RIGHT NOW. Be concise — "
    "this is a spoken suggestion, not an essay or explanation. Respond with "
    "ONLY the suggested answer itself: no preamble, no meta-commentary, no "
    "restating the question."
)


def build_suggestion_prompt(question: str, context_turns: list[dict]) -> str:
    """
    question: the extracted question text (Extraction.text where
    type == "question") to suggest an answer for.
    context_turns: recent [{"speaker": ..., "text": ...}] turns, oldest
    first, for grounding. Phase 3 has no knowledge base yet (that's Phase
    6) — this is conversation context only, not retrieved facts, so the
    model is working from what it already knows plus what's been said out
    loud in this session.
    """
    if context_turns:
        context_lines = "\n".join(f"{t['speaker']}: {t['text']}" for t in context_turns)
        context_block = f"Recent conversation:\n{context_lines}\n\n"
    else:
        context_block = ""

    return f"{context_block}Question just asked: {question}\n\nSuggested answer:"
