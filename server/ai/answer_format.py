"""
Phase 5 (2026-08-16): answer-format classification — does a detected
question need a real CODE answer, a spoken PROSE answer, or is it
genuinely unclear which?

Approach, deliberately: rule-based (keyword/regex) first, not an LLM
call, same philosophy as conversation/intent.py's extractor and for the
same reasons — it's a legitimate baseline (not a placeholder), it's
zero extra latency/cost on every single detected question, and it sets
the floor other approaches get compared against rather than assumed
better than.

This matters more here than it did for extraction: the whole point of
this classifier is to make the expensive path (generating BOTH a code
and a prose answer) rare, not routine. If classification itself required
an LLM call, "detect ambiguity cheaply" would be self-defeating — you'd
be paying for an extra round-trip on every question just to decide
whether to pay for a second one. A fast, free, good-enough classifier
that's honest about what it doesn't know (routes the genuinely unclear
cases to CODE_AND_PROSE rather than guessing) is the right trade here.

Looking at Ed's real screenshots (2026-08-16 session), almost nothing is
actually ambiguous — "write a function in Python 3 called...",
"implement an LRU cache with O(1) get and put", "build a rate limiter"
are unambiguous code asks the moment you look for the right signals:
an explicit language/library name, a data-structure/algorithm term, or
an explicit imperative-to-build-something verb aimed at those nouns.
Genuine ambiguity ("how would you handle race conditions here?" with no
"write"/"implement"/language attached) should be the minority case, and
this classifier is built so that minority reliably falls into CODE_AND_PROSE
rather than being forced into a wrong guess either direction.
"""

import re

CODE = "code"
PROSE = "prose"
CODE_AND_PROSE = "code_and_prose"

ALL_FORMATS = (CODE, PROSE, CODE_AND_PROSE)

# Explicit "build/write/implement X" imperative verbs aimed at making
# something — strong signal on their own even with zero other markers,
# since these verbs are rarely used for a spoken/conceptual answer.
_BUILD_VERBS = re.compile(
    r"\b(write|implement|build|design|code|refactor)\b",
    re.IGNORECASE,
)

# Programming languages / frameworks named explicitly — near-certain
# code ask regardless of anything else in the sentence.
_LANGUAGE_SIGNALS = re.compile(
    r"\b(python|javascript|typescript|java\b|c\+\+|c#|golang|go\b|rust|"
    r"sql|websockets?|django|flask|react|node(?:\.js)?|api\b)\b",
    re.IGNORECASE,
)

# Data structures / algorithms / CS-specific terms — strong code signal
# even without a named language (e.g. "implement an LRU cache").
_DS_ALGO_SIGNALS = re.compile(
    r"\b(lru cache|hash ?map|linked list|binary (search |)tree|"
    r"time complexity|space complexity|O\(1\)|O\(n\)|O\(log ?n\)|"
    r"rate limiter|cache invalidation|race conditions?|"
    r"concurren\w+|thread[- ]safe|data structures?|algorithms?|"
    r"function that|class that|endpoint that)\b",
    re.IGNORECASE,
)

# An explicit ask for code itself ("a couple code snippets", "show me
# the code") — this wins outright regardless of build-verb wording,
# since there's no ambiguity left once code is asked for by name.
_EXPLICIT_CODE_REQUEST = re.compile(
    r"\bcode snippets?\b|\bshow (me )?(the |some )?code\b|\bwrite (some |the )?code\b",
    re.IGNORECASE,
)

# Phrases that signal a spoken/conceptual answer is wanted, not code —
# these push toward PROSE even in a technical conversation, since they
# ask for judgment/experience/approach rather than a working artifact.
_PROSE_SIGNALS = re.compile(
    r"\b(tell me about a time|describe a time|walk( me)? through your "
    r"experience|how did you handle|why did you|what('s| is) your "
    r"approach to|what do you think about|in your opinion|how would you "
    r"explain)\b",
    re.IGNORECASE,
)


def classify(question_text: str) -> str:
    """
    Returns one of CODE, PROSE, or CODE_AND_PROSE.

    Deliberately conservative in both directions: a clear code signal
    (language name, build verb + CS term) wins even alongside a mild
    prose signal, since "how would you implement X" is still a code ask
    despite starting like a prose one. Genuinely mixed or signal-free
    text falls to CODE_AND_PROSE rather than being forced — this is the
    "narrow ambiguous band" the dual-generation path exists for, and it
    should stay narrow. A question with zero technical signal at all
    (e.g. "what's your availability next week?") is NOT ambiguous, it's
    just plain PROSE — CODE_AND_PROSE is only for cases with real,
    conflicting or absent-but-technical-context signal, not every
    non-technical question, so this function checks for at least some
    technical anchoring before ever returning CODE_AND_PROSE.
    """
    text = question_text or ""

    has_explicit_code_request = bool(_EXPLICIT_CODE_REQUEST.search(text))
    has_build_verb = bool(_BUILD_VERBS.search(text))
    has_language = bool(_LANGUAGE_SIGNALS.search(text))
    has_ds_algo = bool(_DS_ALGO_SIGNALS.search(text))
    has_prose_signal = bool(_PROSE_SIGNALS.search(text))

    # An explicit ask for code by name ("a couple code snippets") wins
    # outright — nothing else in the sentence can make this NOT a code
    # ask once code itself has been requested directly.
    if has_explicit_code_request:
        return CODE

    # Strong, unambiguous code ask: an explicit build verb combined with
    # either a named language or a CS-specific term ("write a function",
    # "implement an LRU cache", "build a rate limiter in Python").
    if has_build_verb and (has_language or has_ds_algo):
        return CODE

    # A named language/library alone, with no prose framing fighting it,
    # is still a reliable code signal even without an explicit verb
    # ("a real-time chat server using WebSockets with clean architecture").
    if has_language and not has_prose_signal:
        return CODE

    # Explicit "tell me about a time"/"how did you handle" framing wins
    # even if a technical noun shows up somewhere in the same sentence —
    # these are asking for a story or approach, not a deliverable.
    if has_prose_signal:
        return PROSE

    # A CS-specific term with no build verb and no prose framing is the
    # real ambiguous case ("how do you think about cache invalidation?"
    # could reasonably be either) — this is deliberately the ONLY path
    # that reaches CODE_AND_PROSE, so it stays a narrow band, not a
    # catch-all for every non-obvious question.
    if has_ds_algo:
        return CODE_AND_PROSE

    # No technical signal at all — ordinary conversational question.
    return PROSE
