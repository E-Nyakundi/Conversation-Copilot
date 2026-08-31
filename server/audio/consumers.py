import asyncio
import json
import logging
import os
import time
from urllib.parse import parse_qs

import numpy as np
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils import timezone

from ai.prompts import DEFAULT_ANSWER_LEVEL, normalize_level
from conversation import intent as intent_module
from conversation.models import Extraction, Session, Suggestion, Turn
from conversation.usage import finalize_session_usage

from . import speaker as speaker_module
from . import vad as vad_module
from . import completion_heuristic as completion_module

logger = logging.getLogger(__name__)

_finalize_session_usage_async = database_sync_to_async(finalize_session_usage)

SAMPLE_RATE = vad_module.SAMPLE_RATE
FRAME_SAMPLES = vad_module.FRAME_SAMPLES
FRAME_BYTES = FRAME_SAMPLES * 2  # int16
FRAME_DURATION_S = FRAME_SAMPLES / SAMPLE_RATE

# Bug fix (2026-08-24, Phase F): static, non-generated spoken companion
# used only when a CODE_AND_PROSE suggestion's prose half fails to
# generate even after one retry (see _generate_suggestion below) — a
# genuine dual-failure of both Groq and the local fallback for that one
# call. Deliberately generic rather than trying to synthesize something
# question-specific without a model: the goal here is just "never let a
# code suggestion ship with literally no spoken companion," not to
# replace the real generated alternative.
_FALLBACK_SPOKEN_ANSWER = (
    "(Spoken answer unavailable right now.) Talk through this yourself: "
    "what the code does, why you wrote it this way, and any trade-offs "
    "or edge cases worth mentioning."
)


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
    # floor (default 800ms, since Danstan's 2026-08-24 request — was
    # briefly raised 600 -> 1000ms per Ed's Phase C decision, see
    # audio/completion_heuristic.py for that history), just via
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
        # Multi-tenancy retrofit (2026-08-30): accounts.ws_auth's
        # TokenAuthMiddleware (wrapping the WebSocket router in
        # copilot/asgi.py) resolves ?token=<key> into self.scope["user"]
        # before this method runs — None if missing/invalid. Reject here
        # rather than in receive() so a bad/absent token never gets as
        # far as accept()ing the connection at all.
        user = self.scope.get("user")
        if user is None:
            await self.accept()  # must accept before close() can send a code+reason on some clients
            await self.send(
                text_data=json.dumps({"type": "error", "reason": "authentication_required"})
            )
            await self.close(code=4001)
            return

        quota = await self._get_quota(user)
        allowed, reason = (False, "account_inactive") if quota is None else quota.can_start_session()
        if not allowed:
            await self.accept()
            await self.send(text_data=json.dumps({"type": "error", "reason": reason}))
            await self.close(code=4003)
            return

        self.user = user
        session_id = self.scope["url_route"]["kwargs"].get("session_id")
        # Session onboarding (2026-08-31, build plan §14): title/mode come
        # in as query params on the WS handshake, same channel as ?token=
        # above — there's no pre-accept header mechanism on a client
        # WebSocket, so this is the one place these have to travel as
        # part of the URL rather than a first message. Only applied when
        # actually creating a new session (see _get_or_create_session) —
        # a resumed session (session_id present, already has its own
        # title/mode from when it was first created) is never overwritten
        # by whatever the reconnect happened to be called with.
        query_params = parse_qs(self.scope.get("query_string", b"").decode())
        title = (query_params.get("title") or [""])[0].strip()[:255]
        mode = (query_params.get("mode") or ["general"])[0].strip()
        if mode not in ("general", "coding", "no_code"):
            mode = "general"
        self.session = await self._get_or_create_session(session_id, user, title, mode)

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
                await _finalize_session_usage_async(self.session)
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
            await _finalize_session_usage_async(self.session)
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
        # NOTE (2026-08-30 multi-tenancy retrofit): quota is checked once,
        # at connect() time, not per-frame here. A session that started
        # under quota can still run past max_minutes if it stays open long
        # enough — enforcement of the *next* session start is airtight
        # (can_start_session() in connect()), but a hard mid-session cutoff
        # once minutes_used + this-session's-elapsed-time crosses
        # max_minutes is NOT implemented yet. Deferred rather than bolted
        # on quickly here because doing it safely means finalizing any
        # in-progress segment and closing cleanly mid-stream, which
        # touches the same state machine _finalize_segment/_process_frame
        # already manage carefully — see the design doc's §6 for the
        # intended approach (a periodic check every N frames) when this
        # gets built.
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
                # floor (MIN_SILENCE_S, default 800ms) with how much the
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
        #
        # Timeout added (2026-08-24, Phase C follow-up): the build plan's
        # own Phase 1 revisit checklist explicitly calls for "if
        # diarization fails OR TIMES OUT for a segment, fall back to the
        # naive pause labeler" — the `except Exception` blocks below only
        # ever covered the "fails" half. A model call that hangs (network
        # stall talking to a lazily-downloaded HF resource, a wedged
        # thread, GPU/CPU contention on this VM's already-tight 2 vCPU/
        # 5.8GB ceiling) would previously block this turn's finalization
        # indefinitely instead of degrading — no exception is ever raised
        # by a hang, so the try/except had nothing to catch. Wrapped both
        # ML branches' `asyncio.to_thread(...)` call in
        # `asyncio.wait_for(..., timeout=DIARIZATION_TIMEOUT_S)`, new env
        # var (default 5.0s — generous for a single-segment embed/label
        # call, per the ~500-600ms Groq suggestion latencies already seen
        # in real sessions, but nowhere near long enough to let a hang
        # stall the whole WebSocket turn). `asyncio.TimeoutError` is
        # caught by the existing `except Exception` (it's an Exception
        # subclass), so the fallback-to-naive path and the existing
        # one-time-per-connection warning flags are reused as-is — no
        # new fallback logic needed, just closing the actual gap in what
        # could reach that fallback in the first place. Note:
        # `asyncio.wait_for` cancels the awaited task on timeout, but the
        # underlying `to_thread` worker thread itself keeps running to
        # completion in the background (Python threads aren't forcibly
        # killable) — harmless here since embed()/label_segment() don't
        # mutate shared state before returning their result, so a late,
        # discarded result from a since-abandoned thread is simply never
        # used, not a race with the naive label already assigned.
        mode = speaker_module.diarization_mode()
        speaker_label = None
        diarize_started = time.time()
        diarize_timeout_s = float(os.environ.get("DIARIZATION_TIMEOUT_S", "5.0"))

        # Minimum audio duration for a reliable speaker embedding
        # (2026-08-24 fix, found via real prod log): a segment-embedding
        # model needs a real stretch of voiced audio to produce a stable
        # voiceprint — short utterances ("So,", "One-one.", a few hundred
        # ms to ~1s) produce noisy, unstable embeddings that don't reliably
        # match ANY prior speaker centroid at a sane similarity threshold,
        # even when spoken by the exact same person turn after turn.
        # Confirmed against a real session: every short turn after the
        # first got assigned a brand-new letter (A, B, C, D, E, F...) —
        # a 4-speaker "conversation" that was actually 1-2 people, once
        # turns got short. This isn't a threshold-tuning problem (no
        # SPEAKER_SIMILARITY_THRESHOLD value fixes fundamentally noisy
        # input) — it's that embedding-based diarization was being run on
        # audio too short for the model to do its job, when the naive
        # pause-gap labeler (which doesn't need voiceprint quality at all)
        # is already the designated fallback and handles short utterances
        # fine. New env var `SPEAKER_EMBED_MIN_S` (default 1.0s, informed
        # by ECAPA/d-vector literature generally wanting >=1-2s of audio,
        # picked as a middle ground rather than the more conservative 2s
        # to avoid over-routing normal short replies to the naive
        # labeler) — segments shorter than this skip the embedding call
        # entirely and go straight to `self.speaker_labeler.label()`,
        # same code path already used for actual embedding failures/
        # timeouts, so no new fallback logic needed, just an earlier exit
        # from the ML path for audio that was never going to be reliable
        # input to it.
        embed_min_s = float(os.environ.get("SPEAKER_EMBED_MIN_S", "1.0"))
        segment_too_short_for_embedding = duration_s < embed_min_s

        if mode == "embedding" and segment_too_short_for_embedding:
            print(
                f"[diarize] mode=embedding skipped (segment {duration_s:.2f}s < "
                f"SPEAKER_EMBED_MIN_S={embed_min_s}s), using naive fallback",
                flush=True,
            )
        elif mode == "embedding":
            try:
                embedding = await asyncio.wait_for(
                    asyncio.to_thread(
                        speaker_module.get_embedder().embed, audio_np, SAMPLE_RATE
                    ),
                    timeout=diarize_timeout_s,
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
                speaker_label = await asyncio.wait_for(
                    asyncio.to_thread(
                        speaker_module.get_diarizer().label_segment, audio_np, SAMPLE_RATE
                    ),
                    timeout=diarize_timeout_s,
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
        from ai.prompts import retrieve_knowledge_context

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

        # Session onboarding (2026-08-31, build plan §14): a user can flag
        # a session as "non-coding" up front (self.session.mode ==
        # "no_code") at connect time — see _get_or_create_session. When
        # that's set, a classifier verdict of CODE or CODE_AND_PROSE is
        # forced down to PROSE. This is a hard override, not a soft nudge:
        # the whole point of the session-level flag is "I already know
        # this session won't have coding questions, don't show me code
        # cards even if some sentence happens to trip the classifier."
        # The reverse case ("coding" mode) is deliberately NOT a hard
        # override the other way — a coding-focused session can still
        # contain genuine behavioral/prose questions, and forcing every
        # answer to CODE_AND_PROSE regardless of content would be wrong
        # in a way the "no_code" case isn't (silence is a safe default;
        # fabricating a code block for "tell me about a conflict you had"
        # is not). "coding" mode is stored but NOT otherwise consumed yet
        # — no prompt-side framing hint, no other behavior change. Left
        # for a future pass if it turns out to matter in practice.
        if self.session.mode == "no_code" and answer_format in (CODE, CODE_AND_PROSE):
            answer_format = PROSE

        # Bug fix (2026-08-24, Phase D): this product is used live, in an
        # oral interview, spoken out loud — Ed's real prod feedback is
        # that a code-only suggestion "feels off," like the candidate is
        # answering as a machine, when the actual expectation is to talk
        # through the answer with understanding of its scope. A plain
        # CODE suggestion with no spoken companion answer is never
        # actually useful here, even for a genuinely unambiguous code
        # ask ("write a function that reverses a linked list") — the
        # candidate still has to SAY something, not just read code aloud.
        # Folding straight CODE into CODE_AND_PROSE at this single call
        # site (rather than changing classify()'s CODE branch itself)
        # keeps ai/answer_format.py's three-way CODE/PROSE/CODE_AND_PROSE
        # distinction intact for anything else that inspects it (tests,
        # future callers, the Suggestion.answer_format field itself still
        # records the classifier's real CODE verdict via the code_result-
        # only degrade path below) while guaranteeing every suggestion
        # actually generated and shown to Ed always carries a spoken
        # alternative alongside any code.
        if answer_format == CODE:
            answer_format = CODE_AND_PROSE

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
            # Phase 6 (2026-08-24): retrieve_knowledge_context is a
            # blocking call (embeds the query, searches FAISS), wrapped
            # in to_thread same as get_suggestion below so it never
            # blocks incoming audio frames on this connection. Already
            # degrades to None internally on any failure/empty knowledge
            # base — see ai/prompts.py — so no separate try/except needed
            # here; None flows straight through to get_suggestion exactly
            # like "no knowledge base configured".
            knowledge_chunks = await asyncio.to_thread(retrieve_knowledge_context, combined_question)

            if answer_format == CODE_AND_PROSE:
                # Two independent generations, run concurrently rather
                # than sequentially — this is already the expensive path
                # by design (see docstring above); no reason to also pay
                # for it serially. Each is wrapped in to_thread since
                # get_suggestion is a blocking call.
                code_result, prose_result = await asyncio.gather(
                    asyncio.to_thread(get_suggestion, combined_question, context_turns, level, CODE, knowledge_chunks, self.session.owner),
                    asyncio.to_thread(get_suggestion, combined_question, context_turns, level, PROSE, knowledge_chunks, self.session.owner),
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

                # Bug fix (2026-08-24, Phase F): a code-only suggestion
                # reaching Ed live is never acceptable — same reasoning as
                # the CODE->CODE_AND_PROSE fold above, it's just that this
                # is the OTHER way a code suggestion could end up with no
                # spoken companion: not the classifier routing it wrong,
                # but the prose half of an already-correctly-routed
                # CODE_AND_PROSE genuinely failing to generate. That was
                # silently degrading to answer_format=CODE with
                # secondary_text=None, which is exactly the "some already
                # do that but some don't" gap Danstan reported — the
                # classifier fix made routing consistent, but a live
                # generation failure could still ship code with no oral
                # alternative. get_suggestion() already tries Groq then
                # the local model internally (see ai/cloud_router.py), so
                # a raised exception here means BOTH already failed once —
                # worth exactly one retry (transient hiccups, e.g. a
                # single empty-response from reasoning-token exhaustion,
                # are the common case per that module's own notes), but
                # never worth blocking the whole suggestion on a second
                # failure. If the retry also fails, fall back to a static,
                # non-generated spoken prompt rather than nothing — Ed
                # still gets *something* telling him to talk through the
                # code himself, instead of a silent code-only suggestion.
                if code_result and not prose_result:
                    print(f"[suggest] extraction {extraction.id}: retrying prose half once", flush=True)
                    try:
                        prose_result = await asyncio.to_thread(
                            get_suggestion, combined_question, context_turns, level, PROSE, knowledge_chunks, self.session.owner
                        )
                    except Exception as e:
                        print(f"[suggest] extraction {extraction.id}: prose retry failed: {e!r}", flush=True)
                        prose_result = None

                if not code_result and not prose_result:
                    raise RuntimeError("both code_and_prose generations failed")

                # If one half genuinely failed (even after the retry
                # above), degrade gracefully rather than aborting the
                # whole suggestion — same "best-effort" posture as the
                # rest of this method — but a failed PROSE half no longer
                # means a bare CODE suggestion: _FALLBACK_SPOKEN_ANSWER
                # (see module-level constant) guarantees a spoken
                # companion ships every time code does, even when actual
                # generation for it is unavailable.
                if code_result and prose_result:
                    result = {"text": code_result["text"], "model": code_result.get("model", "")}
                    secondary_text = prose_result["text"]
                elif code_result:
                    result = code_result
                    secondary_text = _FALLBACK_SPOKEN_ANSWER
                else:
                    answer_format = PROSE
                    result = prose_result
                    secondary_text = None
            else:
                result = await asyncio.to_thread(
                    get_suggestion, combined_question, context_turns, level, answer_format, knowledge_chunks, self.session.owner
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
    @database_sync_to_async
    def _get_quota(user):
        return getattr(user, "quota", None)

    @staticmethod
    async def _get_or_create_session(session_id, owner, title="", mode="general"):
        """
        Multi-tenancy retrofit (2026-08-30): session_id resume is now
        scoped to `owner` — looking up a session that exists but belongs
        to a different tenant behaves exactly like "not found" (falls
        through to creating a new one), rather than leaking another
        tenant's session id/state to whoever guesses or reuses it.

        Session onboarding (2026-08-31, build plan §14): title/mode are
        only used on the create path — a resumed existing session keeps
        whatever it was already created with, since a reconnect's own
        query params reflect the resuming client's current defaults, not
        necessarily what the session should still be called.
        """
        if session_id:
            try:
                return await Session.objects.aget(pk=session_id, owner=owner)
            except Session.DoesNotExist:
                pass
        return await Session.objects.acreate(owner=owner, title=title, mode=mode)