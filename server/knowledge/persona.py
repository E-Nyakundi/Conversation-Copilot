"""
Phase 6.5 (2026-08-24): builds the compact, always-injected persona/voice
summary described in conversation/models.py's UserProfile.persona_summary
docstring — distinct from knowledge/documents.py:retrieve_context, which
is relevance-gated per turn. This module answers a different question:
not "what does the KB say that's relevant right now" but "what does the
KB tell us about how this person talks and thinks, in general."

Deliberately a dedicated one-off summarization call, not routed through
ai/prompts.py's build_suggestion_prompt/SUGGESTION_SYSTEM_PROMPT — those
are tuned for real-time spoken-answer brevity ("not an essay"), which is
the wrong shape for "write me a paragraph characterizing this person's
voice." Reuses ai.cloud_router's lazy-singleton Groq-client pattern
directly (_get_groq_client) rather than get_suggestion() itself, since
this is a distinct kind of call with its own prompt and no local-LLM
fallback requirement — a rebuild is a deliberate, infrequent, on-demand
action (see regenerate_persona management command), not something that
needs to survive a Groq outage the way live suggestions do.
"""

import logging
import os

logger = logging.getLogger(__name__)

# Kept short deliberately: this is meant to be a distilled characterization,
# not a second copy of the knowledge base. It gets prepended to every
# prompt (see ai/prompts.py:get_system_prompt), so its size is a standing
# token-budget cost paid on every single turn — unlike retrieve_context's
# chunks, which are only pulled in when a turn's question actually needs
# them.
PERSONA_SUMMARY_MAX_WORDS = 150

PERSONA_SUMMARY_SYSTEM_PROMPT = (
    "You distill a person's voice and personality from their own notes "
    "and documents into a short characterization another writer could "
    "use to sound like them. Focus on tone, values, recurring stances "
    "and opinions, and how they tend to phrase things — not a summary "
    "of the documents' subject matter. Write it as direct instructions "
    "to a writer (e.g. \"They speak plainly and get to the point...\"), "
    f"in {PERSONA_SUMMARY_MAX_WORDS} words or fewer."
)


def _collect_source_text() -> str:
    """
    Pulls together the text the summary gets built from. Uses every
    Document.text currently in the knowledge base rather than only
    already-embedded Chunks — this call doesn't need retrieval/FAISS at
    all, just the raw text, so it's unaffected by chunking/indexing
    state (a Document ingested but not yet chunked/indexed still
    contributes here).

    No length cap applied at this layer — build_persona_summary below
    truncates if needed, so this function stays a simple, honest "here's
    everything" and doesn't duplicate that policy.
    """
    from knowledge.models import Document

    texts = list(Document.objects.order_by("created_at").values_list("text", flat=True))
    return "\n\n---\n\n".join(t for t in texts if t and t.strip())


def build_persona_summary() -> str:
    """
    Builds a fresh persona summary from every Document currently in the
    knowledge base, via one Groq summarization call. Returns "" (not an
    exception) if there's no source material to summarize yet or the
    call fails — same "absence means silently unchanged" posture as
    ai/prompts.py's build_identity_context and _build_knowledge_block:
    a persona-summary failure should degrade UserProfile back to
    identity-only prompts, never take down suggestion generation.

    Caller (regenerate_persona management command) is responsible for
    persisting the result onto UserProfile.persona_summary +
    persona_summary_updated_at — this function only builds the text, it
    doesn't touch the DB, mirroring knowledge/documents.py:ingest vs.
    retrieve_context's separation of "does the work" from "is called by
    a thin wrapper."

    Deliberately NOT run automatically on every knowledge/document
    ingest — a persona summary is meant to characterize the whole KB in
    aggregate, and rebuilding it (an LLM call) on every single upload
    would be wasteful for a KB that grows one document at a time. It's
    an explicit, on-demand rebuild instead (see the management command).
    """
    source_text = _collect_source_text()
    if not source_text.strip():
        logger.info("No knowledge-base documents to build a persona summary from.")
        return ""

    # Source text truncated to a generous character budget before it
    # even reaches the model — a large KB could otherwise blow past the
    # model's context window on this one call. Not chunked/RAG'd here on
    # purpose: this call wants breadth (a feel for the whole KB), not
    # the highest-relevance-to-a-query slice retrieve_context looks for.
    max_chars = int(os.environ.get("PERSONA_SUMMARY_MAX_SOURCE_CHARS", "24000"))
    if len(source_text) > max_chars:
        source_text = source_text[:max_chars]

    try:
        from ai.cloud_router import _get_groq_client

        client = _get_groq_client()
        model = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": PERSONA_SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": source_text},
            ],
            max_tokens=400,
            temperature=0.3,
            reasoning_effort=os.environ.get("GROQ_REASONING_EFFORT", "low"),
        )
    except Exception:
        logger.warning("Persona summary generation failed (Groq call error).", exc_info=True)
        return ""

    choice = response.choices[0].message.content if response.choices else None
    if not choice or not choice.strip():
        logger.warning("Persona summary generation returned an empty response.")
        return ""

    return choice.strip()
