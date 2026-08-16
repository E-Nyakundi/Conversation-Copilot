"""
Phase 2 (2026-08-16): LLM-based extraction — real semantic understanding
of a turn, as an opt-in alternative to conversation/intent.py's rule-
based extractor, not a replacement for it.

Why this exists and what it actually changes: intent.py's regex
extractor is lexical — it matches surface patterns (a "?", a wh-word, an
imperative verb from a fixed list). It has no notion of what a sentence
*means*. That's a real, previously-stated limitation, not a bug — see
intent.py's own docstring. This module hands the same job to an LLM
instead, which can catch things regex structurally cannot: an implied
question with no "?" and no wh-word ("I'm curious how you'd approach
that" / "the capital of Minnesota, right?" used as a genuine question),
a decision phrased without any of DECISION_PATTERNS' keywords, sarcasm
or negation regex can't reason about, etc. That's the actual
"comprehension" gap the build plan flagged under Extraction Accuracy as
"stay with regex vs. move to Groq-based extraction — no decision made
yet."

This is NOT a strict upgrade. Trade-offs, stated plainly:
  - Latency: a real API round-trip per finalized turn, vs. regex's
    effectively-zero cost. On a live session this runs on every turn,
    not just ones with detected suggestions — that's a meaningfully
    different cost profile than ai/cloud_router.py's per-question calls.
  - Cost: one more Groq call per turn.
  - A different, not strictly better, failure mode: regex fails closed
    (misses things) in predictable, inspectable ways. An LLM extractor
    can also hallucinate items that aren't really there, or apply
    inconsistent judgment call to call — that has to be measured, not
    assumed away. Hence EXTRACTION_MODE defaulting to "rule_based" (see
    audio/consumers.py) rather than switching over silently.
  - No offline/no-network story. intent.py has zero dependencies, works
    with GROQ_API_KEY unset, works if the network's down. This doesn't.

Mirrors ai/cloud_router.py's lazy-singleton + deferred-import pattern so
importing this module doesn't require `groq` installed or
GROQ_API_KEY configured just to run `manage.py check` etc.
"""

import json
import logging
import os
import threading

from conversation.intent import ALL_TYPES, ExtractedItem

logger = logging.getLogger(__name__)

_groq_client = None
_groq_lock = threading.Lock()

EXTRACTION_SYSTEM_PROMPT = (
    "You analyze one turn of a live spoken conversation for a real-time "
    "assistant. Identify every genuine question, decision, request, "
    "objection, and action item actually being expressed — including "
    "ones phrased without a question mark or an obvious keyword, the "
    "way people actually talk. Do NOT invent items that aren't really "
    "there just to have something in every category. A turn can "
    "produce zero items, one, or several. Respond with ONLY a JSON "
    "object, no other text, in exactly this shape:\n"
    '{"items": [{"type": "question|decision|request|objection|'
    'action_item", "text": "<verbatim substring of the turn>", '
    '"confidence": <0.0-1.0>}], "topic_change": <true|false>}\n'
    "confidence reflects your genuine certainty this is really that "
    "type of item, not a fixed value — vary it honestly. topic_change "
    "is true only if this turn clearly moves to a substantively "
    "different subject than what came immediately before it."
)


def _get_groq_client():
    """Thread-safe lazy singleton, same shape as ai.cloud_router's."""
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


def _build_user_prompt(text: str, previous_text: str | None) -> str:
    if previous_text:
        return f'Previous turn: "{previous_text}"\n\nThis turn: "{text}"'
    return f'This turn (first in the conversation, or previous turn unknown): "{text}"'


def _parse_response(raw: str, source_text: str) -> list[ExtractedItem]:
    """
    Raises ValueError on anything malformed — extract() below decides
    whether that means "fall back to rule-based" or propagate, same
    posture as ai/cloud_router.py's "only genuinely Groq-specific
    failures get silently handled" principle. A model that returns
    garbage JSON is a different problem than a network timeout and
    callers may want to know the difference.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM extraction returned non-JSON: {raw[:200]!r}") from e

    items = []
    for raw_item in parsed.get("items", []):
        item_type = raw_item.get("type")
        if item_type not in ALL_TYPES or item_type == "topic_change":
            # Model hallucinated a type outside the allowed set, or put
            # topic_change in the items list instead of the dedicated
            # top-level field we asked for — skip rather than raise, a
            # single malformed item shouldn't discard everything else
            # the model got right in the same response.
            logger.warning("LLM extraction: skipping item with invalid type %r", item_type)
            continue
        text = raw_item.get("text", "").strip()
        if not text:
            continue
        try:
            confidence = float(raw_item.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        items.append(ExtractedItem(type=item_type, text=text, confidence=round(confidence, 2)))

    if parsed.get("topic_change"):
        items.append(ExtractedItem(
            type="topic_change",
            text=source_text,
            confidence=0.6,  # model gives no confidence for this field; fixed value, same as intent.py's own topic_change detector
        ))

    return items


def extract(text: str, previous_text: str | None = None) -> list[ExtractedItem]:
    """
    Same signature and return shape as conversation.intent.extract(), so
    audio/consumers.py can call this instead with no other changes on
    the caller's side (see EXTRACTION_MODE there).

    Raises on any failure — the caller decides whether to fall back to
    the rule-based extractor or let it propagate. Deliberately not
    swallowing errors here the way ai/cloud_router.py doesn't swallow
    non-Groq-specific failures: a prompt bug should surface, not get
    silently absorbed as "well, regex still ran."
    """
    from groq import APIConnectionError, APIError, APITimeoutError, RateLimitError

    client = _get_groq_client()
    model = os.environ.get("EXTRACTION_GROQ_MODEL", os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant"))

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(text, previous_text)},
            ],
            max_tokens=400,
            temperature=0.2,  # lower than suggestion generation (0.4) — this is classification, not creative generation; want consistent judgment call to call
            response_format={"type": "json_object"},
        )
    except (APIConnectionError, APITimeoutError, RateLimitError, APIError) as e:
        raise RuntimeError(f"LLM extraction request failed: {e}") from e

    choice = response.choices[0].message.content if response.choices else None
    if not choice or not choice.strip():
        raise RuntimeError("LLM extraction returned an empty response")

    return _parse_response(choice.strip(), text)
