import asyncio
import json
import logging
import os
import time

import numpy as np
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils import timezone

from ai.prompts import DEFAULT_ANSWER_LEVEL, normalize_level
from conversation import intent as intent_module
from conversation.models import Extraction, Session, Suggestion, Turn

from . import speaker as speaker_module
from . import vad as vad_module
from . import completion_heuristic as completion_module

logger = logging.getLogger(__name__)

SAMPLE_RATE = vad_module.SAMPLE_RATE
FRAME_SAMPLES = vad_module.FRAME_SAMPLES
FRAME_BYTES = FRAME_SAMPLES * 2  # int16
FRAME_DURATION_S = FRAME_SAMPLES / SAMPLE_RATE


class PingConsumer(AsyncWebsocketConsumer):
    """
    Phase 0 feasibility consumer.

    The phone sends {"client_sent_at": <ms epoch>} and this echoes it back
    with a server timestamp added, so the client can compute:
      - client -> server latency (server_received_at - client_sent_at)
      - full round trip (now - client_sent_at)

    Test this over cellular data, not home wifi, to get a realistic number.
    This consumer is intentionally trivial — it exists only to validate the
    Cloudflare Tunnel + Channels/WebSocket path before Phase 1 adds real
    audio streaming.
    """

    async def connect(self):
        await self.accept()

    async def disconnect(self, close_code):
        pass

    async def receive(self, text_data=None, bytes_data=None):
        server_received_at = time.time() * 1000

        try:
            payload = json.loads(text_data)
        except (TypeError, ValueError):
            payload = {}

        response = {
            "client_sent_at": payload.get("client_sent_at"),
            "server_received_at": server_received_at,
            "server_sent_at": time.time() * 1000,
        }
        await self.send(text_data=json.dumps(response))


class AudioStreamConsumer(AsyncWebsocketConsumer):
    """
    Phase 1: real audio ingestion.

    Protocol:
      - Client connects to ws/audio/ (new session) or
        ws/audio/<session_id>/ (resume an existing session).
      - Server sends {"type": "session_started", "session_id": "..."}
        immediately on connect.
      - Client streams raw mono PCM16LE audio at SAMPLE_RATE (16000Hz) as
        binary WebSocket frames. Any chunk size is fine — frames are
        re-buffered into fixed 512-sample windows for VAD internally.
      - Server runs VAD frame-by-frame:
          - on speech -> buffers audio
          - on ~600ms of silence following speech -> finalizes the
            segment: runs STT, labels a speaker, persists a Turn, and
            emits {"type": "final", "turn_id": ..., "speaker": ...,
            "text": ..., "confidence": ..., "started_at": ...,
            "ended_at": ...}
      - Client may send {"type": "end_session"} as a text frame to close
        out the session explicitly (flushes any in-progress segment).

    No "partial"/live-preview transcript is sent while someone is mid-turn
    (there used to be one — see the removal note where PARTIAL_INTERVAL_S
    used to live, below). VAD itself still runs per-frame via
    asyncio.to_thread, which is cheap; STT only runs once per finalized
    segment.
    """

    # Config (env-overridable — see server/.env.example)
    #
    # Phase C (2026-08-23): the old fixed SILENCE_S class attribute is
    # removed. Finalization now uses completion_heuristic.py's adaptive
    # threshold (MIN_SILENCE_S floor + a bounded, completion-score-based
    # extension) instead of a single constant read once at class-body
    # eval time — see _process_frame. VAD_SILENCE_MS still controls the
    # floor (default raised 600 -> 1000ms per Ed's decision), just via
    # completion_heuristic.MIN_SILENCE_S rather than a class attribute
    # here.
    MIN_SEGMENT_S = float(os.environ.get("MIN_SEGMENT_MS", "250")) / 1000.0

    # Removed: PARTIAL_INTERVAL_S / PARTIAL_WINDOW_S + the "partial"
    # message they drove. Partials re-decoded the *entire* accumulated
    # segment through local Whisper at beam_size=5 every ~1.2s for as
    # long as someone kept talking — and did so on the same 2 vCPUs as
    # everything else. They were purely a cosmetic "still typing" UI
    # touch (the extraction/suggestion pipeline only ever reads the
    # final segment, never a partial), so that CPU was being spent on a
    # nicety while starving the thing that actually matters: how fast a
    # real suggestion shows up. That's also the direct cause of lag that
    # *compounded* the longer a conversation ran, since every frame on a
    # connection is awaited in order — once partial-decoding fell behind
    # real-time, there was nothing to let it catch back up. If live
    # partial transcripts are wanted again later, do it as an actual
    # incremental/streaming decode rather than a full-window re-decode
    # on a timer.
    BACKPRESSURE_MAX_BACKLOG_S = float(os.environ.get("BACKPRESSURE_MAX_BACKLOG_MS", "2000")) / 1000.0

    async def connect(self):
        session_id = self.scope["url_route"]["kwargs"].get("session_id")
        self.session = await self._get_or_create_session(session_id)

        self._pending = b""
        self.speech_active = False
        self.segment_frames = []
        self.segment_start_wall = None
        self.segment_gap_s = 0.0
        self.silence_tail_s = 0.0
        self.gap_since_last_turn_s = 0.0
        # Phase C (2026-08-23): tracks the trailing completion signal for
        # the in-progress segment's silence — see
        # audio/completion_heuristic.py. Recreated each time a new
        # segment starts (see _process_frame), read while waiting out the
        # silence tail to decide whether this particular pause justifies
        # finalizing near the floor or should be given more time.
        self.completion_tracker = completion_module.CompletionTracker()
        self.speaker_labeler = speaker_module.NaivePauseSpeakerLabeler()
        # Phase 1 revisit (2026-08-14): session-persistent speaker
        # identity — see audio/speaker.py's module docstring. One
        # registry per connection, same scope as speaker_labeler above.
        self.speaker_registry = speaker_module.SessionSpeakerRegistry()
        self.last_turn_text = None  # Phase 2: topic_change needs the previous turn's text
        # 2026-08-16 fix (build plan §11a): speaker.py now caches the
        # underlying construction failure itself (so it stops re-attempting
        # a doomed import every turn), but the per-turn print below was
        # still firing on every single turn regardless — real prod
        # evidence: identical "[diarize] mode=embedding failed..." lines
        # on every turn of a whole session. These two flags let each mode
        # print its failure once per *connection* instead of once per
        # turn; the actual naive-fallback behavior is unchanged either way.
        self._diarize_embedding_warned = False
        self._diarize_pyannote_full_warned = False
        # Phase 4: answer-level switching. Session-wide, not per-question —
        # a client-sent "set_answer_level" message (see receive()) changes
        # this for every suggestion generated from here on, until changed
        # again. Starts at the default so a client that never sends the
        # message gets identical behavior to before Phase 4.
        self.answer_level = DEFAULT_ANSWER_LEVEL
        # Phase 3: suggestion generation runs as fire-and-forget background
        # tasks (see _extract_and_emit / _generate_suggestion) rather than
        # inline, since local LLM inference can take several seconds even
        # on decent CPU hardware — awaiting it inline would stall audio
        # ingestion for this connection for the whole generation. Tracked
        # here so disconnect() can cancel any still-running ones cleanly.
        self._background_tasks = set()

        await self.accept()
        await self.send(
            text_data=json.dumps({"type": "session_started", "session_id": str(self.session.id)})
        )

    async def disconnect(self, close_code):
        # Best-effort: mark the session ended if the client dropped without
        # sending an explicit end_session message. Any in-flight segment is
        # simply discarded rather than force-finalized on a dying connection.
        for task in list(self._background_tasks):
            task.cancel()
        if getattr(self, "session", None) and self.session.ended_at is None:
            self.session.ended_at = timezone.now()
            try:
                await self.session.asave()
            except Exception:
                pass  # DB may be unavailable on shutdown; don't raise in disconnect

    async def receive(self, text_data=None, bytes_data=None):
        if bytes_data is not None:
            await self._handle_audio_chunk(bytes_data)
            return

        try:
            payload = json.loads(text_data)
        except (TypeError, ValueError):
            return

        if payload.get("type") == "end_session":
            await self._finalize_segment()  # flush anything in progress
            self.session.ended_at = timezone.now()
            await self.session.asave()
            await self.send(text_data=json.dumps({"type": "session_ended"}))
            await self.close()
            return

        if payload.get("type") == "set_answer_level":
            # Phase 4: Quick/Medium/Detailed switching. Silently normalized
            # rather than rejected — see ai.prompts.normalize_level — so a
            # stale/misspelled client value degrades to the default instead
            # of breaking suggestion generation.
            self.answer_level = normalize_level(payload.get("level"))
            await self.send(
                text_data=json.dumps(
                    {"type": "answer_level_set", "level": self.answer_level}
                )
            )
            return

    # -- audio pipeline -----------------------------------------------------

    async def _handle_audio_chunk(self, bytes_data: bytes):
        from stt.engine import pcm16_bytes_to_float32

        self._pending += bytes_data

        # Backpressure: every frame on this connection is awaited in
        # order (see class docstring), so if processing ever falls
        # behind the rate audio is arriving at — a slow box, CPU
        # contention from another session, a VAD hiccup — _pending would
        # otherwise grow without bound and every subsequent frame stays
        # exactly as far behind real time as the last one, forever. Cap
        # the backlog instead: once more than BACKPRESSURE_MAX_BACKLOG_S
        # of raw audio is buffered, drop the oldest excess (frame-aligned)
        # so this connection resyncs to *recent* audio instead of slowly
        # working through an ever-growing queue of stale audio. This
        # trades a small chunk of audio (skipped VAD/possible mid-word
        # cut) for bounded, self-recovering latency.
        max_backlog_bytes = round(self.BACKPRESSURE_MAX_BACKLOG_S * SAMPLE_RATE) * 2  # int16 = 2 bytes/sample
        if len(self._pending) > max_backlog_bytes:
            excess = len(self._pending) - max_backlog_bytes
            excess -= excess % FRAME_BYTES  # keep the buffer frame-aligned
            if excess > 0:
                logger.warning(
                    "session %s: audio backlog exceeded %.1fs, dropping ~%.2fs "
                    "of buffered audio to catch up",
                    getattr(self.session, "id", "?"),
                    self.BACKPRESSURE_MAX_BACKLOG_S,
                    excess / (SAMPLE_RATE * 2),
                )
                self._pending = self._pending[excess:]

        while len(self._pending) >= FRAME_BYTES:
            frame_bytes = self._pending[:FRAME_BYTES]
            self._pending = self._pending[FRAME_BYTES:]
            frame_f32 = pcm16_bytes_to_float32(frame_bytes)
            await self._process_frame(frame_f32)

    async def _process_frame(self, frame_f32: np.ndarray):
        # Phase C (2026-08-23): need the raw probability now, not just
        # the thresholded bool — is_speech's own threshold check still
        # decides speech-vs-silence exactly as before, but the raw value
        # also feeds the completion tracker below.
        prob = await asyncio.to_thread(vad_module.speech_prob, frame_f32)
        vad_threshold = float(os.environ.get("VAD_THRESHOLD", "0.5"))
        is_speech = prob >= vad_threshold

        if is_speech:
            if not self.speech_active:
                self.speech_active = True
                self.segment_frames = []
                self.segment_start_wall = timezone.now()
                self.segment_gap_s = self.gap_since_last_turn_s
                # Fresh tracker for this new segment — a previous
                # segment's trailing trend has no bearing on this one.
                self.completion_tracker = completion_module.CompletionTracker()

            self.segment_frames.append(frame_f32)
            self.completion_tracker.add_frame(frame_f32, prob)
            self.silence_tail_s = 0.0
        else:
            if self.speech_active:
                self.segment_frames.append(frame_f32)
                self.completion_tracker.add_frame(frame_f32, prob)
                self.silence_tail_s += FRAME_DURATION_S
                # Phase C: the fixed SILENCE_S threshold is replaced by
                # an adaptive one — completion_tracker blends the raised
                # floor (MIN_SILENCE_S, default 1000ms) with how much the
                # trailing audio actually looks like a real stopping
                # point (RMS + speech-prob decay) versus a mid-thought
                # pause, up to a bounded extension so an ambiguous tail
                # still finalizes eventually rather than hanging. See
                # audio/completion_heuristic.py's module docstring for
                # the full reasoning and the honest not-yet-validated
                # caveat on the decay-score weighting itself.
                effective_threshold = self.completion_tracker.effective_silence_threshold()
                if self.silence_tail_s >= effective_threshold:
                    await self._finalize_segment()
            else:
                self.gap_since_last_turn_s += FRAME_DURATION_S

    async def _finalize_segment(self):
        from stt.cloud_router import get_final_transcription

        if not self.speech_active or not self.segment_frames:
            self.speech_active = False
            return

        # Trim the silence tail that was accumulated while waiting for
        # the effective (now adaptive, Phase C) threshold to elapse —
        # feeding trailing silence to Whisper degrades accuracy and adds
        # unnecessary latency. self.silence_tail_s reflects real elapsed
        # silence regardless of what threshold triggered finalization.
        silence_frames = round(self.silence_tail_s / FRAME_DURATION_S)
        speech_frames = self.segment_frames[:-silence_frames] if silence_frames else self.segment_frames
        audio_np = np.concatenate(speech_frames) if speech_frames else np.concatenate(self.segment_frames)
        duration_s = audio_np.shape[0] / SAMPLE_RATE
        segment_start_wall = self.segment_start_wall
        segment_gap_s = self.segment_gap_s

        self.speech_active = False
        self.segment_frames = []
        self.silence_tail_s = 0.0

        if duration_s < self.MIN_SEGMENT_S:
            # Too short to be real speech (VAD blip) — discard.
            # Reset gap so the accumulated silence before this blip doesn't
            # inflate the gap seen by the next real turn's speaker labeler.
            self.gap_since_last_turn_s = 0.0
            return

        # get_final_transcription doesn't share stt.engine's "__default__"
        # sentinel/env plumbing (see stt/cloud_router.py docstring) — read
        # WHISPER_LANGUAGE here so the Groq path honors the same forced-
        # language behavior the local engine defaults to (env unset -> "en").
        language = os.environ.get("WHISPER_LANGUAGE", "en").strip() or None
        result = await asyncio.to_thread(get_final_transcription, audio_np, language)
        text = result["text"]
        if not text:
            self.gap_since_last_turn_s = 0.0
            return

        # Phase 1 revisit (2026-08-14): mode-dispatched speaker labeling.
        # See audio/speaker.py's module docstring for what each mode is.
        # Any ML-based mode falls back to the naive pause labeler for
        # *this turn* on failure/timeout rather than dropping the speaker
        # label entirely, per the build plan's explicit instruction.
        mode = speaker_module.diarization_mode()
        speaker_label = None
        diarize_started = time.time()

        if mode == "embedding":
            try:
                embedding = await asyncio.to_thread(
                    speaker_module.get_embedder().embed, audio_np, SAMPLE_RATE
                )
                if embedding is not None:
                    speaker_label = self.speaker_registry.assign(embedding)
                print(
                    f"[diarize] mode=embedding {(time.time() - diarize_started) * 1000:.0f}ms "
                    f"label={speaker_label!r}",
                    flush=True,
                )
            except Exception as exc:
                if not self._diarize_embedding_warned:
                    print(f"[diarize] mode=embedding failed, falling back to naive: {exc!r}", flush=True)
                    self._diarize_embedding_warned = True
                speaker_label = None
        elif mode == "pyannote_full":
            try:
                speaker_label = await asyncio.to_thread(
                    speaker_module.get_diarizer().label_segment, audio_np, SAMPLE_RATE
                )
                print(
                    f"[diarize] mode=pyannote_full {(time.time() - diarize_started) * 1000:.0f}ms "
                    f"label={speaker_label!r}",
                    flush=True,
                )
            except Exception as exc:
                if not self._diarize_pyannote_full_warned:
                    print(f"[diarize] mode=pyannote_full failed, falling back to naive: {exc!r}", flush=True)
                    self._diarize_pyannote_full_warned = True
                speaker_label = None

        if not speaker_label:
            speaker_label = self.speaker_labeler.label(segment_gap_s)

        ended_wall = timezone.now()
        turn = await Turn.objects.acreate(
            session=self.session,
            speaker=speaker_label,
            text=text,
            started_at=segment_start_wall,
            ended_at=ended_wall,
            confidence=result["confidence"],
        )

        await self.send(
            text_data=json.dumps(
                {
                    "type": "final",
                    "turn_id": str(turn.id),
                    "speaker": speaker_label,
                    "text": text,
                    "confidence": result["confidence"],
                    "started_at": segment_start_wall.isoformat(),
                    "ended_at": ended_wall.isoformat(),
                }
            )
        )
        self.gap_since_last_turn_s = 0.0

        await self._extract_and_emit(turn)
        self.last_turn_text = text

    async def _extract_and_emit(self, turn: Turn):
        """
        Phase 2: run the extractor on the just-finalized turn and
        persist/stream anything it finds. Runs after the `final` message so
        a slow or failing extraction never delays the transcript itself —
        extraction is best-effort on top of a working transcript, not a
        blocker for one.

        EXTRACTION_MODE (2026-08-16) — "rule_based" (default), "llm", or
        "hybrid":
        rule_based is conversation/intent.py's regex extractor: zero extra
        latency, zero dependencies, works with no network — this is what's
        actually been tested across every session so far, and what the
        Phase 2 go/no-go scoring was run against.

        "llm" is conversation/llm_extract.py — a real Groq call per
        finalized turn that can catch questions/decisions/etc. phrased in
        ways no regex pattern can (see that module's docstring for the
        honest trade-offs: latency, cost, a different failure mode, no
        offline story). This is opt-in, not a silent upgrade — switching
        the default would mean shipping something with zero verification
        against real sessions, the same mistake almost made with the
        speaker diarization config earlier in this build.

        "hybrid" (2026-08-16, added per the recon findings after two more
        missing-verb bugs turned up in the same session — see build plan
        §11a): runs rule_based first (same zero-latency path as always),
        and only calls llm_extract for that turn when rule_based returns
        *zero* items. This is deliberately not "always run both" — the
        whole point is to keep paying rule_based's near-zero cost on the
        turns it already handles correctly (the large majority, per every
        session logged so far) and only pay the Groq round-trip on turns
        it's blind to. It will NOT catch every gap rule_based has (a turn
        that's a genuine zero — "okay", "thank you" — also triggers the
        LLM call, since this module has no way to distinguish "correctly
        found nothing" from "missed something"; that's an accepted cost,
        not a bug), but it does mean a future missing-verb-shaped gap like
        "discuss"/"cover" wouldn't have silently produced zero suggestions
        for an entire prompt the way it did this session — llm_extract
        would have caught it the same turn, not three "repeat that
        question"s later. Still opt-in via env, same posture as "llm"
        above — not the default until it's been run against a real
        session and the extra latency/cost on genuine-zero turns has been
        judged acceptable.

        On any llm-mode (or hybrid's llm fallback) failure, this falls
        back to rule_based's result for that turn rather than losing
        extraction entirely — same "never take the session down over
        this" posture as the outer try/except below.
        """
        extraction_mode = os.environ.get("EXTRACTION_MODE", "rule_based")

        try:
            if extraction_mode == "llm":
                from conversation import llm_extract

                try:
                    found = await asyncio.to_thread(llm_extract.extract, turn.text, self.last_turn_text)
                except Exception as e:
                    print(
                        f"[extract] turn {turn.id}: llm extraction failed ({e!r}), "
                        "falling back to rule_based for this turn",
                        flush=True,
                    )
                    found = await asyncio.to_thread(intent_module.extract, turn.text, self.last_turn_text)
            elif extraction_mode == "hybrid":
                found = await asyncio.to_thread(intent_module.extract, turn.text, self.last_turn_text)
                if not found:
                    from conversation import llm_extract

                    try:
                        llm_found = await asyncio.to_thread(
                            llm_extract.extract, turn.text, self.last_turn_text
                        )
                        if llm_found:
                            print(
                                f"[extract] turn {turn.id}: rule_based found nothing, "
                                f"llm fallback found {len(llm_found)} item(s)",
                                flush=True,
                            )
                        found = llm_found
                    except Exception as e:
                        print(
                            f"[extract] turn {turn.id}: hybrid llm fallback failed ({e!r}), "
                            "keeping rule_based's empty result for this turn",
                            flush=True,
                        )
            else:
                found = await asyncio.to_thread(intent_module.extract, turn.text, self.last_turn_text)
        except Exception as e:
            # Extraction is additive; never take the session down over it.
            # Still printed (not just logger.exception) because this app has
            # no console log handler configured for normal-severity
            # messages — see the note on _generate_suggestion below, same
            # reasoning applies here.
            print(f"[extract] turn {turn.id}: extraction raised {e!r}", flush=True)
            return

        print(
            f"[extract] turn {turn.id} ({turn.text!r}): found {len(found)} item(s) "
            f"{[(i.type, round(i.confidence, 2)) for i in found]}",
            flush=True,
        )

        if not found:
            return

        extraction_objs = [
            Extraction(
                session=self.session,
                turn=turn,
                type=item.type,
                text=item.text,
                confidence=item.confidence,
                subtype=item.subtype,
            )
            for item in found
        ]
        await asyncio.to_thread(Extraction.objects.bulk_create, extraction_objs)

        await self.send(
            text_data=json.dumps(
                {
                    "type": "extraction",
                    "turn_id": str(turn.id),
                    "items": [
                        {"type": item.type, "text": item.text, "confidence": item.confidence}
                        for item in found
                    ],
                }
            )
        )

        # Phase 3: fire off (don't await) suggestion generation. See the
        # note on self._background_tasks in connect() for why this is a
        # background task rather than inline.
        #
        # Multi-question merge (2026-08-23): every `question`-type
        # Extraction from THIS turn is grouped into one list and handed to
        # a single _generate_suggestion call, instead of one independent
        # task per Extraction row. See that method's docstring for the
        # full rationale — the short version: conversation/intent.py
        # splits a turn sentence-by-sentence, so a single utterance with
        # several genuinely distinct sub-questions used to fire N siloed
        # suggestion calls with zero shared awareness of each other. All N
        # Extraction rows are still created individually above (bulk_create
        # already ran) — only generation is merged. The common case (one
        # question per turn, still the large majority per every session
        # logged so far) produces a list of length 1 and behaves exactly
        # as before.
        question_extractions = [
            extraction_obj
            for extraction_obj, item in zip(extraction_objs, found)
            if item.type == intent_module.QUESTION
        ]
        if question_extractions:
            task = asyncio.create_task(self._generate_suggestion(turn, question_extractions))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

    async def _recent_turns_context(self, limit: int) -> list[dict]:
        """Last `limit` turns for this session, oldest first, as plain
        dicts — used to ground the suggestion prompt (see ai/prompts.py).
        Plain sync ORM query pushed to a thread rather than using the
        async ORM, matching the pattern used everywhere else in this file.

        Filters out very short/fragment turns (default: under 3 words)
        before returning. Rationale (build plan, "Small notes — what's
        actually left"): a stray one-word turn like "August." sitting in
        this window can get latched onto by the model as the answer to an
        unrelated question, even though a real conversation's context
        should have made the actual answer obvious. Filtering happens
        here rather than in ai/prompts.py so the fetch and the policy
        live together, and so prompts.py stays a pure formatter.

        Note: filtering happens *before* `limit` is applied to the
        underlying query — we over-fetch by a small multiple and drop
        fragments client-side, so a run of short turns doesn't quietly
        starve the context window down to fewer than `limit` usable
        turns.
        """
        min_words = int(os.environ.get("CONTEXT_MIN_WORDS", "3"))
        overfetch = limit * 3

        def _fetch():
            qs = self.session.turns.order_by("-started_at")[:overfetch]
            turns = [{"speaker": t.speaker, "text": t.text} for t in reversed(list(qs))]
            filtered = [t for t in turns if len(t["text"].split()) >= min_words]
            return filtered[-limit:]

        return await asyncio.to_thread(_fetch)

    async def _generate_suggestion(self, turn: Turn, extractions: list[Extraction]):
        """
        Phase 3 (v4): suggestion generation for one detected question, via
        the hybrid router — Groq first (ai.cloud_router), local LLM
        fallback. Runs as a background task (see connect()/
        _extract_and_emit) so it never blocks incoming audio frames on
        this connection. Best-effort: a missing/misconfigured Groq key
        and local model (ai.local_llm.get_llm() raises FileNotFoundError
        until LLM_MODEL_PATH is set — see .env.example) or any inference
        failure just means no suggestion, not a crashed session.

        Phase 5 (2026-08-16): answer format routing. ai.answer_format
        classifies the question as CODE, PROSE, or CODE_AND_PROSE (see
        that module — cheap rule-based classification, deliberately not
        an LLM call, so this stays fast for the common case). CODE and
        PROSE are still exactly one generation call each, just with a
        different prompt/token-budget pair. CODE_AND_PROSE is the one
        case that costs a second call — by design this should be a small
        minority of questions, not the default path; see
        ai/answer_format.py's docstring for why it's built to stay narrow.

        Multi-question merge (2026-08-23, per real prod evidence — see
        build plan): `extractions` is a LIST, not a single Extraction.
        conversation/intent.py splits a turn sentence-by-sentence, so a
        single utterance containing several genuinely distinct
        sub-questions ("what stays serverless? what moves to dedicated
        services? how do you handle auth, caching, ...?") used to produce
        one Extraction row PER sentence and one independent, siloed
        _generate_suggestion call per row — no call had any idea the
        others existed, so answers could overlap, contradict, or read as
        unrelated fragments of what a human would treat as one question.
        _extract_and_emit now groups every `question`-type Extraction
        from the same turn into a single list and this method builds ONE
        combined prompt from all of them (see combined_question below) —
        exactly one suggestion, addressing every part together, the way
        a person actually answering would. All N Extraction rows are
        still persisted individually (so /extractions/ and the admin
        stay accurate per-sub-question) — only suggestion GENERATION is
        merged, not extraction itself. The group's first Extraction is
        used as the representative row for the Suggestion FK/WS payload
        (Suggestion.extraction is nullable by design for exactly this
        kind of one-to-many case — see models.py). The common single-
        question case (len(extractions) == 1, still the large majority)
        is unaffected: combined_question is just that one question's
        text, byte-for-byte the same prompt as before this change.

        Diagnostic prints throughout (not just logger calls) are
        deliberate: this project has no LOGGING config in settings.py, so
        logger.info/debug calls have nowhere configured to go, and even
        logger.warning/exception only reach the console via Python's
        bare-minimum "last resort" stderr handler — easy to miss and easy
        to mistake for "nothing happened" when actually something did.
        print(..., flush=True) is the same pattern the startup warmup
        messages already use, and those are confirmed to show up.
        """
        from ai.answer_format import CODE, CODE_AND_PROSE, PROSE, classify
        from ai.cloud_router import get_suggestion

        extraction = extractions[0]

        # A single question stays exactly what it always was — no numbered
        # list, no framing, byte-for-byte the same prompt this method sent
        # before the multi-question merge existed. Only genuinely multiple
        # sibling questions from the same turn get the numbered framing
        # below, so the model treats them as parts of one ask rather than
        # answering the first and ignoring the rest.
        if len(extractions) == 1:
            combined_question = extraction.text
        else:
            numbered = "\n".join(f"{i}. {e.text}" for i, e in enumerate(extractions, start=1))
            combined_question = (
                "The following were all asked together, as parts of one "
                f"question:\n{numbered}\n\nAddress all parts together in one "
                "coherent answer, not as separate unrelated answers."
            )

        # Snapshot the level at the moment generation starts, not when it
        # finishes — if the client switches levels mid-generation, this
        # in-flight suggestion should still reflect what was asked for
        # when it kicked off, not whatever the level happens to be later.
        level = self.answer_level

        answer_format = classify(combined_question)

        print(
            f"[suggest] extraction {extraction.id} ({combined_question!r}): "
            f"starting (level={level}, format={answer_format}, "
            f"merged={len(extractions)})",
            flush=True,
        )

        context_limit = int(os.environ.get("SUGGESTION_CONTEXT_TURNS", "6"))

        # Imperative asks ("write a function that...", "tell me about a
        # time you...") are routinely set up across several prior turns —
        # constraints, background — before the imperative sentence itself
        # lands. An ordinary question ("what time is it?") doesn't need
        # that; widen the window only when intent.py actually tagged this
        # extraction as an imperative match (see Extraction.subtype).
        # Separate env var (not a multiplier on context_limit) so each
        # can be tuned independently once real usage data exists.
        # Code-shaped asks get the same wider window as imperative asks
        # for the same reason — constraints/signature/behavior are often
        # spread across several prior turns before the ask itself lands
        # (see Ed's real screenshots: language, then constraints, then
        # the actual "implement X" turn).
        #
        # Multi-question merge (2026-08-23): checked across the WHOLE
        # group, not just extractions[0] — the representative extraction
        # used for FK/id purposes elsewhere in this method isn't
        # necessarily the one that happened to match the imperative
        # pattern. A group where sub-question #2 or #3 is imperative-
        # shaped still needs the wider window even if #1 wasn't.
        any_imperative = any(e.subtype == "imperative" for e in extractions)
        if any_imperative or answer_format in (CODE, CODE_AND_PROSE):
            context_limit = int(
                os.environ.get("SUGGESTION_CONTEXT_TURNS_IMPERATIVE", "12")
            )

        started = time.monotonic()
        try:
            context_turns = await self._recent_turns_context(context_limit)

            if answer_format == CODE_AND_PROSE:
                # Two independent generations, run concurrently rather
                # than sequentially — this is already the expensive path
                # by design (see docstring above); no reason to also pay
                # for it serially. Each is wrapped in to_thread since
                # get_suggestion is a blocking call.
                code_result, prose_result = await asyncio.gather(
                    asyncio.to_thread(get_suggestion, combined_question, context_turns, level, CODE),
                    asyncio.to_thread(get_suggestion, combined_question, context_turns, level, PROSE),
                    return_exceptions=True,
                )
                # Either half can fail independently (a Groq hiccup on
                # one call shouldn't sink the other) — treat a raised
                # exception as "that half produced nothing" rather than
                # aborting the whole suggestion.
                if isinstance(code_result, Exception):
                    print(f"[suggest] extraction {extraction.id}: code half failed: {code_result!r}", flush=True)
                    code_result = None
                if isinstance(prose_result, Exception):
                    print(f"[suggest] extraction {extraction.id}: prose half failed: {prose_result!r}", flush=True)
                    prose_result = None

                if not code_result and not prose_result:
                    raise RuntimeError("both code_and_prose generations failed")

                # If one half genuinely failed, degrade gracefully to
                # whichever succeeded rather than sending a suggestion
                # with an empty half — same "best-effort" posture as the
                # rest of this method.
                if code_result and prose_result:
                    result = {"text": code_result["text"], "model": code_result.get("model", "")}
                    secondary_text = prose_result["text"]
                elif code_result:
                    answer_format = CODE
                    result = code_result
                    secondary_text = None
                else:
                    answer_format = PROSE
                    result = prose_result
                    secondary_text = None
            else:
                result = await asyncio.to_thread(
                    get_suggestion, combined_question, context_turns, level, answer_format
                )
                secondary_text = None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[suggest] extraction {extraction.id}: generation failed: {e!r}", flush=True)
            logger.exception("Suggestion generation failed for extraction %s", extraction.id)
            return
        latency_ms = (time.monotonic() - started) * 1000

        if not result or not result.get("text"):
            print(f"[suggest] extraction {extraction.id}: empty result, nothing to send", flush=True)
            return

        print(
            f"[suggest] extraction {extraction.id}: got answer from "
            f"{result.get('model')} in {latency_ms:.0f}ms (format={answer_format})",
            flush=True,
        )

        suggestion = await Suggestion.objects.acreate(
            session=self.session,
            turn=turn,
            extraction=extraction,
            question_text=combined_question,
            answer_text=result["text"],
            answer_format=answer_format,
            secondary_answer_text=secondary_text,
            model_used=result.get("model", ""),
            latency_ms=latency_ms,
            answer_level=level,
        )

        try:
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "suggestion",
                        "suggestion_id": str(suggestion.id),
                        "turn_id": str(turn.id),
                        "extraction_id": extraction.id,
                        # Multi-question merge (2026-08-23): the full set of
                        # Extraction ids this one suggestion actually
                        # answers — extraction_id above stays the single
                        # representative row (existing clients that only
                        # read extraction_id keep working unchanged), this
                        # is additive so anything that cares which
                        # sub-questions got merged (a future UI grouping
                        # view, /extractions/ cross-referencing) doesn't
                        # have to guess from turn_id alone. Always present,
                        # even for the common single-question case, so the
                        # shape is consistent rather than conditional.
                        "extraction_ids": [e.id for e in extractions],
                        "question": combined_question,
                        "answer": result["text"],
                        "answer_format": answer_format,
                        # Populated only for code_and_prose: the companion
                        # prose answer alongside answer_text's code answer.
                        # Flutter can ignore this field entirely until it's
                        # ready to render a second variant — omitting it
                        # for prose/code suggestions rather than sending
                        # null keeps the payload shape closer to what
                        # existing clients already expect.
                        **({"secondary_answer": secondary_text} if secondary_text else {}),
                        "latency_ms": round(latency_ms),
                        "answer_level": level,
                        # Extraction confidence, not model confidence — the
                        # rule-based (or future LLM) extractor's own
                        # confidence that this text was actually a
                        # question worth answering. Deliberately NOT a
                        # fabricated LLM self-confidence score: the build
                        # plan (Phase 7 notes) explicitly defers that until
                        # it can be paired with a cheaper proxy signal
                        # rather than trusted alone.
                        #
                        # Multi-question merge (2026-08-23): the MIN across
                        # the whole group, not just the representative
                        # extraction's own value — a merged suggestion is
                        # only as trustworthy as its weakest-confidence
                        # sub-question; taking the max or the first would
                        # silently hide that one part of the merged answer
                        # was a shakier extraction than the others.
                        "extraction_confidence": min(e.confidence for e in extractions if e.confidence is not None)
                        if any(e.confidence is not None for e in extractions)
                        else None,
                    }
                )
            )
        except Exception:
            # Connection may have closed while generation was in flight —
            # the Suggestion row is already persisted either way.
            pass

    # -- session lookup -------------------------------------------------

    @staticmethod
    async def _get_or_create_session(session_id):
        if session_id:
            try:
                return await Session.objects.aget(pk=session_id)
            except Session.DoesNotExist:
                pass
        return await Session.objects.acreate()