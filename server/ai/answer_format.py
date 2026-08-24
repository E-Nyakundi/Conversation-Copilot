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
#
# Bug fix (2026-08-24, Phase C): "design", "build", and "develop" are
# common as NOUNS/adjectives too ("design philosophy", "product
# development", "a well-designed system") and, unlike "write"/
# "implement"/"code"/"refactor" (which are essentially always verbs in
# practice), a bare \b match on the word alone was catching these noun
# uses and misrouting plain opinion/background questions ("what's your
# favorite design philosophy?") to CODE_AND_PROSE with zero technical
# signal behind it. Root cause confirmed against a real prod turn:
# "How would you design a real-time notification system? Think about
# delivery guarantees, offline cases, and scaling" — an architecture/
# judgment question with no code request, no DS/algo term, and no
# language — got flagged as a build verb purely because "design"
# appeared anywhere in the string. That particular sentence's eventual
# CODE_AND_PROSE routing was arguably still the right *outcome* per this
# module's own "broad build ask benefits from both" reasoning below, but
# arriving there via a coincidental noun/verb match rather than a real
# imperative-verb signal is the wrong mechanism, and it fails outright on
# sentences where "design"/"build"/"develop" is unambiguously a noun.
#
# Fix: for design/build/develop specifically, require an imperative
# construction — the verb near the start of the sentence/clause (optionally
# after a modal like "how would you", "can you", "could you"), OR directly
# preceded by "to"/"and" (infinitive/coordinated imperative: "...and design
# a caching layer"). This is deliberately narrower than a bare \b match,
# not broader — write/implement/code/refactor keep the original bare match
# since they don't have this noun-collision problem.
_BUILD_VERBS_UNAMBIGUOUS = re.compile(
    r"\b(write|implement|code|refactor)\b",
    re.IGNORECASE,
)
_BUILD_VERBS_IMPERATIVE_ONLY = re.compile(
    r"(^\s*(?:how would you|how do you|can you|could you|would you|please)?\s*"
    r"(?:design|architect|build|develop|create)\b"
    r"|\b(?:to|and)\s+(?:design|architect|build|develop|create)\b)",
    re.IGNORECASE,
)


def _has_build_verb(text: str) -> bool:
    return bool(_BUILD_VERBS_UNAMBIGUOUS.search(text)) or bool(
        _BUILD_VERBS_IMPERATIVE_ONLY.search(text)
    )

# Command-style algorithm verbs ("reverse a linked list", "detect a
# cycle", "check if X is a palindrome") — deliberately NOT folded into
# _BUILD_VERBS above: these words are common in ordinary prose too
# ("check if the client agreed", "find out what happened"), so on
# their own they're too weak a signal and stay OUT of the bare-verb
# CODE_AND_PROSE fallback. They only count when paired with an actual
# DS/algo term already anchoring the sentence to something technical —
# see the CODE rule below.
_ALGO_COMMAND_VERBS = re.compile(
    r"\b(reverse|detect|check|determine|calculate|compute|merge|sort|"
    r"search|traverse|invert|validate|parse|count|reduce|flatten|"
    r"rotate|sum|find)\b",
    re.IGNORECASE,
)

# Programming languages / frameworks / infra named explicitly — near-
# certain code ask regardless of anything else in the sentence.
_LANGUAGE_SIGNALS = re.compile(
    r"\b(python|javascript|typescript|java\b|c\+\+|c#|golang|go\b|rust|"
    r"kotlin|swift|php|ruby|scala|bash|shell script|"
    r"sql|websockets?|django|flask|react|node(?:\.js)?|api\b|"
    r"docker|kubernetes|k8s|redis|mongodb|postgres(?:ql)?|mysql|"
    r"kafka|rabbitmq|grpc|graphql|aws|s3|lambda function)\b",
    re.IGNORECASE,
)

# Data structures / algorithms / CS-specific terms — strong code signal
# even without a named language (e.g. "implement an LRU cache").
_DS_ALGO_SIGNALS = re.compile(
    r"\b(lru cache|hash ?maps?|hash ?tables?|linked lists?|"
    r"binary (search |)trees?|binary search\b|"
    r"stacks?\b|queues?\b|priority queue|heaps?\b|tries?\b|graphs?\b|"
    r"dfs\b|bfs\b|depth[- ]first|breadth[- ]first|"
    r"dynamic programming|memoi[sz]ation|greedy algorithm|"
    r"sliding window|two[- ]pointer|binary search|sort(?:ing)? algorithm|"
    r"time complexity|space complexity|big[- ]o\b|O\(1\)|O\(n\)|O\(log ?n\)|"
    r"rate limiter|cache invalidation|race conditions?|deadlocks?|"
    r"mutex(?:es)?|semaphores?|distributed lock|consistent hashing|"
    r"sharding|idempoten\w+|pub[- /]sub|message queue|event loop|"
    r"design pattern|singleton pattern|"
    r"concurren\w+|thread[- ]safe|data structures?|algorithms?|"
    r"recursion|recursive(?:ly)?|palindrome|anagram|merge intervals?|"
    r"function that|class that|endpoint that|given an array|given a string)\b",
    re.IGNORECASE,
)

# An explicit ask for code itself ("a couple code snippets", "show me
# the code", "write pseudocode") — this wins outright regardless of
# build-verb wording, since there's no ambiguity left once code is
# asked for by name.
_EXPLICIT_CODE_REQUEST = re.compile(
    r"\bcode snippets?\b|\bshow (me )?(the |some |your )?(code|implementation)\b|"
    r"\bwrite (some |the )?(code|pseudocode)\b|\bpseudocode\b",
    re.IGNORECASE,
)

# Phrases that signal a spoken/conceptual answer is wanted, not code —
# these push toward PROSE even in a technical conversation, since they
# ask for judgment/experience/approach rather than a working artifact.
#
# Phase B (2026-08-23): widened from Ed's original list. The original
# list was narrow enough that ordinary background/experience questions
# mentioning a bare tech noun ("What's your experience with our API
# design culture?", "Have you used Django before?", "How familiar are
# you with React?") had NO prose signal, NO build verb, and NO DS/algo
# term — only a bare _LANGUAGE_SIGNALS hit — and so fell through to the
# has_language-alone CODE rule below, misclassifying plain conversational
# questions as code asks. Added common experience/familiarity/opinion
# framings that carry a tech noun but are asking about background, not
# a deliverable.
_PROSE_SIGNALS = re.compile(
    r"\b(tell me about a time|describe a time|walk( me)? through your "
    r"experience|how did you handle|why did you|what('s| is) your "
    r"approach to|what do you think about|in your opinion|how would you "
    r"explain|what('s| is) your experience with|have you (used|worked "
    r"with)|how familiar are you with|how comfortable are you with|"
    r"do you (like|enjoy) working with|"
    r"what do you like about|what('s| is) it like working with|"
    r"why (do|did) you (choose|prefer|like)|what appeals to you about|"
    r"how would you compare|which do you prefer)\b",
    re.IGNORECASE,
)

# Bug fix (2026-08-24, Phase D): a describe/explain-style question can
# still contain a real build verb + language inside its own subordinate
# clause — "Describe a Python or bash script you'd write to find and
# delete files older than 30 days..." has "write" + "python"/"bash" and
# was reaching the build-verb+language CODE rule below despite the
# governing verb of the whole question being "describe", not "write".
# Confirmed against a real prod turn (2026-08-24 make prod-logs session):
# this is an oral interview asking the candidate to talk through their
# approach, not to hand back an actual script — Ed had to ask the
# interviewer to rephrase because the suggestion came back as a code
# block. The existing _PROSE_SIGNALS list only covers "describe a time"
# (behavioral framing), not "describe a script/function/system you'd
# write" (technical-but-still-oral framing) — a real gap, not an
# overlapping case.
#
# Deliberately narrow, same posture as every other signal in this file:
# only fires when the sentence's OWN governing verb is describe/explain/
# walk-me-through and it's aimed at a build verb inside a relative
# clause ("...you'd write", "...you would implement", "...you'd build").
# Doesn't touch a bare "describe" with no downstream build verb (that
# already falls through to plain PROSE with no technical anchor) or a
# leading build verb with no describe/explain framing (still CODE, as
# it should be — "write a function that reverses a linked list" is a
# real code ask, not a description-of-a-script ask).
_DESCRIBE_YOUR_APPROACH = re.compile(
    r"\b(describe|explain|walk( me)? through)\b[^.?!]{0,80}\b"
    r"(you('d| would)|you will)\s+(write|implement|build|design|code)\b",
    re.IGNORECASE,
)


def classify(question_text: str) -> str:
    """
    Returns one of CODE, PROSE, or CODE_AND_PROSE.

    Deliberately conservative in both directions: a clear code signal
    (build verb + language/CS term, or an explicit code request) wins
    even alongside a mild prose signal, since "how would you implement
    X" is still a code ask despite starting like a prose one. Genuinely
    mixed, signal-free-but-build-verbed, DS/algo-flavored-but-verbless,
    or bare-language-mention-only text all fall to CODE_AND_PROSE rather
    than being forced either direction — this is the "narrow ambiguous
    band" the dual-generation path exists for. A question with zero
    technical signal at all (e.g. "what's your availability next week?")
    is NOT ambiguous, it's just plain PROSE.

    Phase B (2026-08-23): a bare language/tech-noun mention with nothing
    else technical in the sentence no longer returns CODE outright — see
    the bare-language rule below for why. This is the fix for Ed's real-
    session report that plain experience/background questions mentioning
    a tech noun were being misrouted to CODE.

    Phase D (2026-08-24): "describe/explain a script you'd write..." no
    longer returns straight CODE just because a build verb + language
    happen to sit inside the sentence's own subordinate clause — see
    _DESCRIBE_YOUR_APPROACH above for the real prod case this fixes.
    """
    text = question_text or ""

    has_explicit_code_request = bool(_EXPLICIT_CODE_REQUEST.search(text))
    has_build_verb = _has_build_verb(text)
    has_algo_command_verb = bool(_ALGO_COMMAND_VERBS.search(text))
    has_language = bool(_LANGUAGE_SIGNALS.search(text))
    has_ds_algo = bool(_DS_ALGO_SIGNALS.search(text))
    has_prose_signal = bool(_PROSE_SIGNALS.search(text))
    has_describe_your_approach = bool(_DESCRIBE_YOUR_APPROACH.search(text))

    # An explicit ask for code by name ("a couple code snippets") wins
    # outright — nothing else in the sentence can make this NOT a code
    # ask once code itself has been requested directly.
    if has_explicit_code_request:
        return CODE

    # Explicit "tell me about a time"/"how did you handle" framing wins
    # even ahead of the build-verb+language rule below, not just ahead of
    # the bare-language rule. Bug found during Phase B fixture testing
    # (2026-08-23): "What's your experience with our API design culture
    # here?" contains "design" (read by _BUILD_VERBS as the imperative
    # verb, though it's actually the noun/adjective in "design culture")
    # alongside "API" (_LANGUAGE_SIGNALS) — that combination used to hit
    # the build-verb+language CODE rule before the prose-signal check ever
    # ran, misclassifying a plain experience question as CODE despite an
    # explicit "what's your experience with" framing sitting right there.
    # Moving this check first means an explicit prose framing always wins
    # over a same-sentence word that merely happens to also be a build
    # verb — this is the same "known transition word" style of gating
    # already used elsewhere in this codebase (see intent.py's
    # _SEQUENCE_PREFIX) to stop a coincidental word match from overriding
    # a clearer, more specific signal.
    if has_prose_signal:
        return PROSE

    # "Describe/explain a script you'd write..." — the question is
    # governed by describe/explain, so it's asking the candidate to talk
    # through their approach out loud, not hand back an artifact. Routed
    # to CODE_AND_PROSE rather than straight PROSE: there's still a real
    # code-shaped answer worth having ready (e.g. as a written reference
    # or for a follow-up "now actually write it"), it just can't be the
    # ONLY suggestion shown for a question that's explicitly asking for
    # spoken description. Checked ahead of the build-verb+language rule
    # below for the same reason has_prose_signal is checked first: a
    # governing describe/explain frame should win over a build verb that
    # merely appears inside the sentence's own subordinate clause.
    if has_describe_your_approach:
        return CODE_AND_PROSE

    # Strong, unambiguous code ask: an explicit build verb combined with
    # either a named language or a CS-specific term ("write a function",
    # "implement an LRU cache", "build a rate limiter in Python"). Also
    # covers terse leetcode-style commands ("reverse a linked list",
    # "detect a cycle in a graph", "check if a string is a palindrome")
    # via _ALGO_COMMAND_VERBS — those verbs are too generic to count on
    # their own (see that regex's docstring), but paired with a real
    # DS/algo term they're just as unambiguous as an explicit "implement".
    if (has_build_verb or has_algo_command_verb) and (has_language or has_ds_algo):
        return CODE

    # Bug fix (2026-08-23, Phase B): a named language/library mentioned on
    # its own — no build verb, no DS/algo term, no explicit code request —
    # used to return CODE outright. That was too confident: real sessions
    # showed plain background/experience questions ("What's your take on
    # our API design?", "Do you like working with Django?") reaching this
    # point with nothing else technical to anchor a CODE call, and getting
    # misclassified. A bare language mention with a real system-flavored
    # noun still narrowly reads as a build ask ("a real-time chat server
    # using WebSockets with clean architecture" has "server"/"architecture"
    # alongside the language, not just the language alone), so route the
    # remaining ambiguous case to CODE_AND_PROSE rather than a wrong
    # confident guess in either direction — consistent with how a bare
    # DS/algo term (below) and a bare build verb (further below) are both
    # already handled.
    if has_language:
        return CODE_AND_PROSE

    # A CS-specific term with no build verb and no prose framing is a
    # real ambiguous case ("how do you think about cache invalidation?"
    # could reasonably be either).
    if has_ds_algo:
        return CODE_AND_PROSE

    # Bug fix (2026-08-16, from Ed's real session): a build verb with NO
    # recognized language or CS term (e.g. "Implement a collaborative
    # document editor with... OT or CRDTs" — "OT"/"CRDTs" aren't in the
    # DS/algo list, and no language is named) was falling all the way
    # through to plain PROSE, which is wrong — this is clearly a build-
    # something ask. Route to CODE_AND_PROSE rather than straight CODE:
    # unlike the narrower DS/algo-term case, a bare build verb with no
    # other technical anchor is more likely to be a broader system-
    # design question that genuinely benefits from both a code sketch
    # and a spoken explanation, not just code alone.
    if has_build_verb:
        return CODE_AND_PROSE

    # No technical signal at all — ordinary conversational question.
    return PROSE
