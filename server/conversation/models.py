import uuid

from django.db import models


class UserProfile(models.Model):
    """
    Phase 5 (2026-08-24): the app's primary-user identity — who the app is
    representing when it suggests an answer. Ed's framing: the app isn't a
    neutral assistant, it's meant to answer AS the user, in first person
    ("tell us about yourself" should come back as the user's own
    background, not a generic third-person description). Injected into
    ai/prompts.py's suggestion prompt as fixed context once populated.

    Deliberately a singleton, not a per-request-user table — this app has
    no login/account system (see the build plan's Phase 3.5 "no login for
    v1" decision) and is architected as one deployment, one user. Enforced
    via a fixed primary key (`id=1` always) rather than a real uniqueness
    constraint on some other field, since there's no natural unique field
    to constrain on for a single free-form profile.

    First-pass capture method (2026-08-24 decision): a Settings screen
    text-entry section, not the conversational-interview or document-
    upload flows Ed also described — those remain real future work, not
    dropped, just not this pass's scope (see the build plan entry for the
    full reasoning).
    """

    SINGLETON_ID = 1

    id = models.IntegerField(primary_key=True, default=SINGLETON_ID, editable=False)

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

    def save(self, *args, **kwargs):
        # Enforce the singleton: always write to the same row regardless
        # of what's passed in, so there's exactly one UserProfile ever.
        self.pk = self.SINGLETON_ID
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls) -> "UserProfile":
        """Fetch the singleton row, creating an empty one on first access."""
        obj, _created = cls.objects.get_or_create(pk=cls.SINGLETON_ID)
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
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255, blank=True)
    mode = models.CharField(max_length=50, default="general")

    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"Session {self.id} ({self.started_at:%Y-%m-%d %H:%M})"


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
