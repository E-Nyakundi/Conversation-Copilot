"""
Phase 2: conversation understanding — extraction of questions, decisions,
requests, objections, action items, and topic changes from finalized Turns.

Approach, deliberately: a rule-based (regex/keyword) extractor first, not an
LLM. Reasons:

  - It's a legitimate baseline, not a placeholder. The build plan's go/no-go
    checkpoint needs *something* to score before an LLM-based extractor is
    worth benchmarking against it — otherwise "is extraction good enough"
    has no reference point.
  - Zero extra deps / instant latency, same philosophy as the naive speaker
    labeler in audio/speaker.py. It runs inline in the WebSocket pipeline
    without adding a model load to the critical path.
  - It sets the floor. When you do benchmark local LLM sizes per the build
    plan, compare their accuracy AND latency against this, not against
    nothing — if a 7B model isn't meaningfully more accurate than regex on
    your real transcripts, that's a real signal, not a reason to feel bad
    about using regex.

Known limitation, stated plainly: this will miss anything that doesn't
match a lexical pattern (e.g. an implied question with no question mark or
wh-word, a decision phrased without a decision verb) and will occasionally
false-positive on similarly-worded small talk. That's exactly what the
Phase 2 scoring step (see conversation/management/commands/score_extraction.py)
is for — don't skip it.

Each finalized Turn's text is split into sentences and each sentence is
classified independently; a single Turn can produce zero, one, or several
ExtractedItems.
"""

import re
from dataclasses import dataclass

QUESTION = "question"
DECISION = "decision"
REQUEST = "request"
OBJECTION = "objection"
ACTION_ITEM = "action_item"
TOPIC_CHANGE = "topic_change"

ALL_TYPES = (QUESTION, DECISION, REQUEST, OBJECTION, ACTION_ITEM, TOPIC_CHANGE)


@dataclass
class ExtractedItem:
    type: str
    text: str
    confidence: float  # rough heuristic strength, 0-1, NOT a calibrated probability
    # None for most matches; "imperative" when QUESTION_PATTERNS' imperative-
    # verb pattern is what matched (see that pattern's own comment). Used
    # downstream (consumers.py) to widen the suggestion context window for
    # these specifically, since coding/behavioral asks are often set up
    # across several prior turns before the imperative sentence itself.
    subtype: str | None = None


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")

_WH_WORDS = r"(who|what|when|where|why|how|which|whose)"
_AUX_START = r"(is|are|was|were|do|does|did|can|could|will|would|should|shall|has|have|had|am)"
_SUBJECT = r"(i|you|we|they|he|she|it|this|that|there)"

QUESTION_PATTERNS = [
    (re.compile(r"\?\s*$"), 0.9, None),
    # Leading wh-word without a question mark (e.g. transcription dropped
    # punctuation). Known false-positive source: subordinate clauses like
    # "When you switch units the totals don't recalculate" also start this
    # way — this pattern can't distinguish the two without more context,
    # which is exactly the kind of gap the scoring command is for.
    (re.compile(rf"^\s*{_WH_WORDS}\b", re.IGNORECASE), 0.6, None),
    # Subject-aux inversion ("Will you...", "Is that...") without a
    # question mark — requires a subject pronoun/determiner right after
    # the aux verb specifically so short acknowledgments like "Will do."
    # don't false-positive on the aux verb alone.
    (re.compile(rf"^\s*{_AUX_START}\s+{_SUBJECT}\b", re.IGNORECASE), 0.45, None),
    # Imperative asks — flagged 2026-08-14 as a real gap: interview-style
    # prompts like "Tell me about a time you..." or "Write a function
    # that..." are asks the copilot needs a suggestion for, but they're
    # commands, not questions — no wh-word, no aux-subject inversion, and
    # usually no "?". Anchored to a small closed set of imperative verbs
    # that are near-universally conversation asks (not to be confused
    # with action-item imperatives like "send me the file", which is why
    # this list stays narrow rather than matching any leading verb).
    #
    # Tagged subtype="imperative" (not just a confidence value) so
    # downstream code can widen the suggestion context window for these
    # specifically — see consumers.py._generate_suggestion. Coding/
    # behavioral prompts are routinely set up across several prior turns
    # (constraints, background) before the actual imperative ask lands,
    # so these need to look back further than an ordinary "what time is
    # it?" question does. Using subtype instead of matching on the 0.65
    # confidence value downstream keeps that coupling explicit rather
    # than implicit/fragile.
    #
    # "walk through"/"talk through" WITHOUT "me" added 2026-08-15 — real
    # spoken system-design prompts routinely drop "me" ("Walk through
    # your trade-offs between false positives, latency, and cost."),
    # and the "me"-required version was silently missing every one of
    # those in testing. Confirmed via screenshots: this exact sentence
    # produced zero extraction matches under the old pattern.
    (re.compile(
        r"^\s*(tell me|describe|explain|walk( me)? through|talk( me)? through|"
        r"give me an example|give an example|write (a|an|some)|"
        r"implement|design|list|name|outline|summarize|compare|define)\b",
        re.IGNORECASE,
    ), 0.65, "imperative"),
]

DECISION_PATTERNS = [
    (re.compile(r"\b(we|i)'ve decided\b", re.IGNORECASE), 0.9),
    (re.compile(r"\b(we|i)('ll| will) go(ing)? with\b", re.IGNORECASE), 0.85),
    (re.compile(r"\blet'?s go with\b", re.IGNORECASE), 0.85),
    (re.compile(r"\b(decided|deciding) to\b", re.IGNORECASE), 0.75),
    (re.compile(r"\bfinal (answer|decision|call) is\b", re.IGNORECASE), 0.8),
    (re.compile(r"\blet'?s (do|go with|move forward with|proceed with)\b", re.IGNORECASE), 0.65),
    (re.compile(r"\bwe're going to\b", re.IGNORECASE), 0.5),
]

REQUEST_PATTERNS = [
    (re.compile(r"\b(can|could|would) you\b.+", re.IGNORECASE), 0.75),
    (re.compile(r"\bwould you mind\b", re.IGNORECASE), 0.8),
    (re.compile(r"\bplease\b", re.IGNORECASE), 0.5),
    (re.compile(r"\bi need you to\b", re.IGNORECASE), 0.85),
    (re.compile(r"\b(send|share|forward) me\b", re.IGNORECASE), 0.6),
    (re.compile(r"\bcould we\b.+", re.IGNORECASE), 0.6),
]

OBJECTION_PATTERNS = [
    (re.compile(r"\bi don'?t (think|agree|believe)\b", re.IGNORECASE), 0.85),
    (re.compile(r"\bi disagree\b", re.IGNORECASE), 0.9),
    (re.compile(r"\bthat won'?t work\b", re.IGNORECASE), 0.85),
    (re.compile(r"\b(my|the) concern is\b", re.IGNORECASE), 0.85),
    (re.compile(r"\b(i'?m|we'?re) (not sure|worried|concerned) (about|that)\b", re.IGNORECASE), 0.75),
    (re.compile(r"\bthe problem (is|with that)\b", re.IGNORECASE), 0.75),
    (re.compile(r"^\s*(but|however)\b", re.IGNORECASE), 0.35),
    (re.compile(r"\bon the other hand\b", re.IGNORECASE), 0.5),
]

ACTION_ITEM_PATTERNS = [
    (re.compile(r"\baction item\b", re.IGNORECASE), 0.95),
    (re.compile(r"\b(i'?ll|i will)\b.+", re.IGNORECASE), 0.6),
    (re.compile(r"\b(you'?ll|you will)\b.+", re.IGNORECASE), 0.55),
    (re.compile(r"\b(we|i) need to\b", re.IGNORECASE), 0.6),
    (re.compile(r"\blet'?s make sure\b", re.IGNORECASE), 0.6),
    (re.compile(r"\bfollow up (on|with)\b", re.IGNORECASE), 0.65),
    (re.compile(r"\bby (monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|end of (day|week))\b", re.IGNORECASE), 0.7),
]

TOPIC_CHANGE_PHRASES = [
    (re.compile(r"^\s*(anyway|anyways)\b", re.IGNORECASE), 0.6),
    (re.compile(r"\b(on a|switching gears|moving on|before I forget|different topic|another (thing|question))\b", re.IGNORECASE), 0.7),
    (re.compile(r"^\s*so,? (moving on|next up)\b", re.IGNORECASE), 0.65),
]

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "to", "of",
    "and", "or", "but", "in", "on", "at", "for", "with", "that", "this",
    "it", "i", "you", "we", "they", "he", "she", "so", "just", "like",
    "do", "does", "did", "have", "has", "had", "not", "no", "yes", "um",
    "uh", "okay", "ok", "well", "really", "actually", "think", "know",
}

# Below this Jaccard-similarity threshold between consecutive turns'
# content words, flag a topic change.
#
# Tuned conservatively on purpose: an early version at 0.12 fired on almost
# every turn in ordinary back-and-forth dialogue, because two short turns
# from different speakers rarely share words even when they're clearly on
# the same topic. Turn-level lexical overlap is a genuinely weak signal
# for topic continuity — this only catches near-total disjointness, and
# even then it's the least reliable of the six extraction types. Don't
# weight it heavily downstream (teleprompter targeting, debrief) without
# rescoring it against your own real transcripts first.
TOPIC_SIMILARITY_THRESHOLD = 0.1
TOPIC_CHANGE_MIN_WORDS = 6  # skip the check on short turns (too noisy below this)


def _split_sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(text) if p.strip()]
    return parts or [text]


def _content_words(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def _classify_sentence(sentence: str) -> list[ExtractedItem]:
    items = []
    for patterns, item_type in (
        (QUESTION_PATTERNS, QUESTION),
        (DECISION_PATTERNS, DECISION),
        (REQUEST_PATTERNS, REQUEST),
        (OBJECTION_PATTERNS, OBJECTION),
        (ACTION_ITEM_PATTERNS, ACTION_ITEM),
    ):
        best_confidence = 0.0
        best_subtype = None
        for entry in patterns:
            # Most pattern lists are (regex, confidence) pairs; only
            # QUESTION_PATTERNS carries a third subtype element (used to
            # flag imperative-ask matches for consumers.py's context
            # window — see the comment there). Handling both shapes here
            # avoids forcing every other pattern list to carry a always-
            # None third field just for this one list's sake.
            pattern, confidence = entry[0], entry[1]
            subtype = entry[2] if len(entry) > 2 else None
            if pattern.search(sentence):
                if confidence > best_confidence:
                    best_confidence = confidence
                    best_subtype = subtype
                elif confidence == best_confidence and subtype:
                    best_subtype = subtype
        if best_confidence > 0:
            items.append(ExtractedItem(
                type=item_type,
                text=sentence,
                confidence=round(best_confidence, 2),
                subtype=best_subtype,
            ))
    return items


def _detect_topic_change(text: str, previous_text: str | None) -> ExtractedItem | None:
    for pattern, confidence in TOPIC_CHANGE_PHRASES:
        if pattern.search(text):
            return ExtractedItem(type=TOPIC_CHANGE, text=text, confidence=confidence)

    if not previous_text:
        return None

    current_words = _content_words(text)
    previous_words = _content_words(previous_text)
    if len(current_words) < TOPIC_CHANGE_MIN_WORDS or len(previous_words) < TOPIC_CHANGE_MIN_WORDS:
        return None

    union = current_words | previous_words
    if not union:
        return None
    similarity = len(current_words & previous_words) / len(union)
    if similarity <= TOPIC_SIMILARITY_THRESHOLD:
        # Confidence scales inversely with similarity — lower overlap reads
        # as a stronger topic-change signal, capped so it never outranks an
        # explicit phrase match above.
        confidence = round(min(0.55, 0.35 + (TOPIC_SIMILARITY_THRESHOLD - similarity)), 2)
        return ExtractedItem(type=TOPIC_CHANGE, text=text, confidence=confidence)
    return None


def extract(text: str, previous_text: str | None = None) -> list[ExtractedItem]:
    """
    Classify a single finalized turn's text. Returns zero or more
    ExtractedItems — sentence-level for question/decision/request/
    objection/action_item, turn-level for topic_change (evaluated once
    against the turn as a whole, compared to previous_text).
    """
    items: list[ExtractedItem] = []
    for sentence in _split_sentences(text):
        items.extend(_classify_sentence(sentence))

    topic_change = _detect_topic_change(text, previous_text)
    if topic_change:
        items.append(topic_change)

    return items
