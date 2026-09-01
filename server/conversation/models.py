import uuid

from django.db import models
from django.utils import timezone


class UserProfile(models.Model):
    """
    The app's primary-user identity — who the app is representing when it
    suggests an answer. Ed's framing: the app isn't a neutral assistant,
    it's meant to answer AS the user, in first person ("tell us about
    yourself" should come back as the user's own background, not a
    generic third-person description). Injected into ai/prompts.py's
    suggestion prompt as fixed context once populated.

    Multi-tenancy retrofit (2026-08-30): was a hardcoded singleton
    (fixed pk=1) back when this app had no login/account system — see
    git history for that version. Now one row per accounts.User via
    `owner`, so each tenant gets their own persona/identity context
    rather than sharing the single deployment-wide profile the old
    design assumed. get_solo() is gone; use get_for_user(user) instead
    — every caller (ai/prompts.py, api/rest_views.py,
    knowledge/management/commands/regenerate_persona.py) was updated to
    pass the request's user through rather than relying on an implicit
    global.
    """

    owner = models.OneToOneField(
        "accounts.User", on_delete=models.CASCADE, related_name="profile", null=True
    )

    name = models.CharField(max_length=150, blank=True)
    role = models.CharField(
        max_length=150,
        blank=True,
        help_text="Job title / role / field, e.g. 'Backend engineer' or 'Sales rep'.",
    )
    # Free-text: career background, personality, whatever the user wants
    # the model to know about them when answering as them. No structure
    # imposed beyond name/role — deliberately open per the 2026-08-24
    # decision to keep the first pass simple.
    bio = models.TextField(
        blank=True,
        help_text="Free-text background, personality, career details — "
        "whatever should inform answers given as this person.",
    )

    updated_at = models.DateTimeField(auto_now=True)

    # Phase 6.5 (2026-08-24): a compact, synthesized voice/personality
    # summary distilled from the knowledge base — NOT raw retrieved
    # chunks. Ed's framing: the KB is "our source of personality for the
    # model" — the model should sound like the person the KB describes
    # on every turn, regardless of whether that turn needs a specific
    # fact from it. That's a different job from knowledge/documents.py's
    # retrieve_context (relevance-gated, per-turn fact lookup), so it
    # gets its own bounded, always-injected field here rather than
    # overloading FAISS retrieval to also carry persona. Built via
    # knowledge/persona.py:build_persona_summary(), cached here (not
    # regenerated per-request) since building it costs an LLM call —
    # see regenerate_persona management command for how it's refreshed.
    persona_summary = models.TextField(
        blank=True,
        help_text="Distilled voice/personality summary built from the knowledge base. "
        "Injected into every prompt alongside identity context, unlike knowledge "
        "retrieval which is relevance-gated per turn.",
    )
    persona_summary_updated_at = models.DateTimeField(null=True, blank=True)

    @classmethod
    def get_for_user(cls, user) -> "UserProfile":
        """Fetch this user's profile row, creating an empty one on first access —
        same "empty profile is a normal state" behavior get_solo() used to
        provide, just scoped per-owner now instead of globally."""
        obj, _created = cls.objects.get_or_create(owner=user)
        return obj

    def is_populated(self) -> bool:
        """
        Whether there's enough here to actually inject into a suggestion
        prompt. An empty/never-touched profile should behave exactly like
        no profile at all, not inject a blank "You are ." context block.
        """
        return bool(self.name.strip() or self.role.strip() or self.bio.strip())

    def __str__(self):
        return self.name or "(unnamed user profile)"


class Session(models.Model):
    """
    A single live-listening session (one WebSocket connection's worth of
    conversation, roughly). Kept deliberately thin for Phase 1 — mode/title
    are here now so Phase 9 (domain modes) and Phase 8 (debrief) don't need
    a schema migration to bolt them on later.

    Session-cap fix (2026-09-01): replaces the old account-wide minutes
    pool (TenantQuota.max_minutes/minutes_used, now removed) with a flat,
    per-session, resetting 2-hour hard ceiling — every session gets its
    own fresh clock from `started_at`, nothing carries over between
    sessions. See SESSION_HARD_CAP_MINUTES /
    SESSION_WARNING_BEFORE_CAP_MINUTES below and the periodic check in
    audio/consumers.py._handle_audio_chunk (the mid-session enforcement
    point; is_past_hard_cap/should_warn are pure helpers used from
    there).
    """

    # Hard ceiling: server force-ends the session at exactly this many
    # minutes elapsed since started_at. Not configurable per tenant —
    # this replaced the old configurable minutes pool by design; see the
    # 2026-09-01 handoff spec.
    SESSION_HARD_CAP_MINUTES = 120
    # Client gets an advance warning this many minutes BEFORE the hard
    # cap — i.e. at the 1h30m mark for a 2-hour cap.
    SESSION_WARNING_BEFORE_CAP_MINUTES = 30
    # Flat, uncounted "bonus" grace period offered (not auto-granted)
    # when a session ends — whether by the user ending early or by the
    # hard cap force-ending it. Same 15 minutes every time; not
    # proportional to time saved, and not charged against anything
    # (there is no pool left to charge it against).
    BONUS_GRACE_MINUTES = 15

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Multi-tenancy retrofit (2026-08-30): every session now belongs to a
    # tenant. Nullable at the DB level only to make the migration onto
    # existing data possible (see accounts/migrations for the backfill
    # step that assigns pre-existing sessions to the first real user,
    # then a follow-up migration tightens this to non-nullable) — every
    # code path that creates a Session going forward must pass owner.
    owner = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="sessions", null=True
    )

    title = models.CharField(max_length=255, blank=True)
    mode = models.CharField(max_length=50, default="general")

    # Session-cap fix (2026-09-01): a bonus session (see BONUS_GRACE_MINUTES
    # above) is a real Session row — turns/extractions/suggestions all work
    # normally — but is flagged so conversation.usage.finalize_session_usage
    # can skip it entirely: it must never increment sessions_used or appear
    # as billable usage. Its own cap is BONUS_GRACE_MINUTES, not
    # SESSION_HARD_CAP_MINUTES (see is_past_hard_cap below) and it is never
    # itself eligible for a further bonus offer when it ends.
    is_bonus = models.BooleanField(default=False)

    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"Session {self.id} ({self.started_at:%Y-%m-%d %H:%M})"

    def elapsed_minutes(self, *, now=None) -> float:
        """Minutes since this session's own started_at — never anything
        carried over from a previous session. `now` is injectable for
        tests; defaults to the actual current time."""
        if not self.started_at:
            return 0.0
        now = now or timezone.now()
        return max(0.0, (now - self.started_at).total_seconds() / 60.0)

    def hard_cap_minutes(self) -> int:
        """The cap that applies to THIS session: a bonus session gets
        BONUS_GRACE_MINUTES (15), a normal session gets
        SESSION_HARD_CAP_MINUTES (120). Kept as a method rather than two
        separate constants read at call sites, so is_past_hard_cap/
        should_warn don't need to know which kind of session they're
        checking."""
        return self.BONUS_GRACE_MINUTES if self.is_bonus else self.SESSION_HARD_CAP_MINUTES

    def is_past_hard_cap(self, *, now=None) -> bool:
        return self.elapsed_minutes(now=now) >= self.hard_cap_minutes()

    def should_warn(self, *, now=None) -> bool:
        """True once elapsed time has crossed the warn-before-cap
        threshold (1h30m for the default 2h cap) but hasn't yet hit the
        hard cap itself — callers are expected to send at most one
        warning (see audio/consumers.py's `_cap_warning_sent` flag).

        A bonus session (15-minute cap) has no separate advance warning
        — its whole window is shorter than the normal warning lead time,
        so this always returns False for one; the client only needs to
        know when a bonus session ends, not get a mid-bonus heads-up."""
        if self.is_bonus:
            return False
        elapsed = self.elapsed_minutes(now=now)
        warn_at = self.SESSION_HARD_CAP_MINUTES - self.SESSION_WARNING_BEFORE_CAP_MINUTES
        return warn_at <= elapsed < self.SESSION_HARD_CAP_MINUTES


class Turn(models.Model):
    """
    One finalized speech segment: VAD detected speech, then silence closed
    it out, and STT produced a transcript for it. Partial/in-progress
    transcripts are streamed over the WebSocket but are NOT persisted here —
    only finalized turns are, to keep this table meaningful for Phase 2+
    (extraction), Phase 5 (memory), and Phase 8 (debrief).
    """

    SPEAKER_UNKNOWN = "unknown"

    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="turns")

    speaker = models.CharField(max_length=50, default=SPEAKER_UNKNOWN)
    text = models.TextField()

    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)

    # Proxy for STT confidence (derived from faster-whisper's avg_logprob /
    # no_speech_prob for this segment). Not a calibrated probability — good
    # enough to flag likely-garbage transcripts for now.
    confidence = models.FloatField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["started_at"]

    def __str__(self):
        preview = self.text[:40] + ("…" if len(self.text) > 40 else "")
        return f"[{self.speaker}] {preview}"


class Extraction(models.Model):
    """
    Phase 2: a single extracted item (question/decision/request/objection/
    action_item/topic_change) found in a finalized Turn's text, via
    conversation/intent.py.

    Kept as its own table rather than a field on Turn since one Turn can
    yield zero, one, or several extractions (a turn is often more than one
    sentence, and topic_change is evaluated turn-wide).
    """

    TYPE_QUESTION = "question"
    TYPE_DECISION = "decision"
    TYPE_REQUEST = "request"
    TYPE_OBJECTION = "objection"
    TYPE_ACTION_ITEM = "action_item"
    TYPE_TOPIC_CHANGE = "topic_change"

    TYPE_CHOICES = [
        (TYPE_QUESTION, "Question"),
        (TYPE_DECISION, "Decision"),
        (TYPE_REQUEST, "Request"),
        (TYPE_OBJECTION, "Objection"),
        (TYPE_ACTION_ITEM, "Action item"),
        (TYPE_TOPIC_CHANGE, "Topic change"),
    ]

    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="extractions")
    turn = models.ForeignKey(
        Turn, on_delete=models.CASCADE, related_name="extractions", null=True, blank=True
    )

    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    text = models.TextField()

    # Rough heuristic strength from the rule-based extractor (0-1), NOT a
    # calibrated probability. Once an LLM-based extractor exists per the
    # build plan's benchmarking step, this same field carries its
    # self-reported confidence too, so downstream consumers don't care
    # which extractor produced a row.
    confidence = models.FloatField(null=True, blank=True)

    # None for most rows; "imperative" when conversation/intent.py's
    # imperative-verb pattern is what matched this question (2026-08-14 —
    # see ExtractedItem.subtype and QUESTION_PATTERNS in intent.py for
    # the full rationale). consumers.py._generate_suggestion reads this
    # to widen the suggestion context window for imperative asks
    # specifically, since those are often set up across several prior
    # turns before the imperative sentence itself lands. Nullable/blank
    # rather than a TYPE_CHOICES-style enum since this is expected to
    # stay a single narrow flag, not grow into a general taxonomy.
    subtype = models.CharField(max_length=20, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        preview = self.text[:40] + ("…" if len(self.text) > 40 else "")
        return f"[{self.type}] {preview}"


class Suggestion(models.Model):
    """
    Phase 3: an AI-generated candidate answer for a detected question
    (Extraction.type == Extraction.TYPE_QUESTION), produced by
    ai/local_llm.py. Single-shot only — no streaming/completion mode (that's
    the gated Stretch phase) and no knowledge-base grounding yet (that's
    Phase 6, wired in later via the same pipeline). extraction is nullable
    so a Suggestion can't be orphaned into a hard failure if its source
    Extraction is ever deleted independently.

    model_used records which local model produced this (e.g. the GGUF
    filename) so Phase 7's cloud fallback can be compared against local
    output side by side later without a schema change.
    """

    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="suggestions")
    turn = models.ForeignKey(Turn, on_delete=models.CASCADE, related_name="suggestions")
    extraction = models.ForeignKey(
        Extraction, on_delete=models.SET_NULL, related_name="suggestions", null=True, blank=True
    )

    LEVEL_QUICK = "quick"
    LEVEL_MEDIUM = "medium"
    LEVEL_DETAILED = "detailed"

    LEVEL_CHOICES = [
        (LEVEL_QUICK, "Quick"),
        (LEVEL_MEDIUM, "Medium"),
        (LEVEL_DETAILED, "Detailed"),
    ]

    question_text = models.TextField()
    answer_text = models.TextField()

    FORMAT_PROSE = "prose"
    FORMAT_CODE = "code"
    FORMAT_CODE_AND_PROSE = "code_and_prose"

    FORMAT_CHOICES = [
        (FORMAT_PROSE, "Prose"),
        (FORMAT_CODE, "Code"),
        (FORMAT_CODE_AND_PROSE, "Code and prose"),
    ]

    # Phase 5 (2026-08-16): which shape this suggestion is — see
    # ai/answer_format.py for how a question gets classified. For
    # FORMAT_CODE_AND_PROSE, answer_text holds the CODE answer (the
    # more actionable of the two for a live coding-interview moment)
    # and secondary_answer_text holds the companion prose answer — kept
    # as one row with two text fields rather than two separate
    # Suggestion rows sharing an extraction, so the UI has one clear
    # place to look for "the other version of this answer" rather than
    # having to correlate two rows itself.
    answer_format = models.CharField(max_length=20, choices=FORMAT_CHOICES, default=FORMAT_PROSE)
    secondary_answer_text = models.TextField(blank=True, null=True)

    # Phase 4: which answer-level (see ai/prompts.py:ANSWER_LEVELS) this
    # suggestion was generated at — recorded per-row (not just used
    # transiently) so Phase 8's debrief can see which level the user
    # actually relied on during a session, and so a level switch mid-
    # session doesn't retroactively mislabel earlier suggestions.
    answer_level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default=LEVEL_MEDIUM)

    model_used = models.CharField(max_length=100, blank=True)
    # Wall-clock generation time in ms — the real-world number that
    # determines whether single-shot suggestions are fast enough to be
    # useful mid-conversation. Worth watching closely on your actual
    # hardware per the build plan's LLM benchmarking step.
    latency_ms = models.FloatField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        preview = self.answer_text[:40] + ("…" if len(self.answer_text) > 40 else "")
        return f"Suggestion: {preview}"
