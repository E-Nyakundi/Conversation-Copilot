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
          - on speech -> buffers audio, periodically emits
            {"type": "partial", "text": "..."} on the growing segment
          - on ~600ms of silence following speech -> finalizes the
            segment: runs STT, labels a speaker, persists a Turn, and
            emits {"type": "final", "turn_id": ..., "speaker": ...,
            "text": ..., "confidence": ..., "started_at": ...,
            "ended_at": ...}
      - Client may send {"type": "end_session"} as a text frame to close
        out the session explicitly (flushes any in-progress segment).

    Deliberately NOT optimized yet: VAD and STT calls run per-frame /
    per-partial via asyncio.to_thread rather than a batched or truly
    streaming decode. Phase 1's goal is a clean, correct end-to-end pipe;
    latency/throughput tuning is a Phase 2 concern once extraction
    accuracy work starts anyway.
    """

    # Config (env-overridable — see server/.env.example)
    SILENCE_S = float(os.environ.get("VAD_SILENCE_MS", "600")) / 1000.0
    MIN_SEGMENT_S = float(os.environ.get("MIN_SEGMENT_MS", "250")) / 1000.0
    PARTIAL_INTERVAL_S = float(os.environ.get("PARTIAL_INTERVAL_MS", "1200")) / 1000.0
    # Partials re-transcribe from scratch each time (Whisper has no true
    # streaming decode here — see the class docstring's "Deliberately NOT
    # optimized yet" note). Without a cap, a long turn's Nth partial
    # re-decodes the *entire* accumulated segment, so cost grows with turn
    # length and every partial gets slower the longer someone talks. Partials
    # are UI-only (the final transcript at silence still decodes the whole
    # trimmed segment — see _finalize_segment) so windowing them to only the
    # most recent audio is safe: it can only affect the live "still typing"
    # preview, never the persisted Turn text or anything extraction reads.
    PARTIAL_WINDOW_S = float(os.environ.get("PARTIAL_WINDOW_MS", "8000")) / 1000.0

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
        self.speech_time_since_partial_s = 0.0
        self.speaker_labeler = speaker_module.NaivePauseSpeakerLabeler()
        self.last_turn_text = None  # Phase 2: topic_change needs the previous turn's text
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
        while len(self._pending) >= FRAME_BYTES:
            frame_bytes = self._pending[:FRAME_BYTES]
            self._pending = self._pending[FRAME_BYTES:]
            frame_f32 = pcm16_bytes_to_float32(frame_bytes)
            await self._process_frame(frame_f32)

    async def _process_frame(self, frame_f32: np.ndarray):
        is_speech = await asyncio.to_thread(vad_module.is_speech, frame_f32)

        if is_speech:
            if not self.speech_active:
                self.speech_active = True
                self.segment_frames = []
                self.segment_start_wall = timezone.now()
                self.segment_gap_s = self.gap_since_last_turn_s
                self.speech_time_since_partial_s = 0.0

            self.segment_frames.append(frame_f32)
            self.silence_tail_s = 0.0
            self.speech_time_since_partial_s += FRAME_DURATION_S

            if self.speech_time_since_partial_s >= self.PARTIAL_INTERVAL_S:
                await self._emit_partial()
                self.speech_time_since_partial_s = 0.0
        else:
            if self.speech_active:
                self.segment_frames.append(frame_f32)
                self.silence_tail_s += FRAME_DURATION_S
                if self.silence_tail_s >= self.SILENCE_S:
                    await self._finalize_segment()
            else:
                self.gap_since_last_turn_s += FRAME_DURATION_S

    async def _emit_partial(self):
        from stt.engine import get_engine

        if not self.segment_frames:
            return
        # Window to the last PARTIAL_WINDOW_S seconds only — see the
        # PARTIAL_WINDOW_S class attribute for why this is safe and
        # necessary. round()-based frame count, floor of 1 frame so a
        # misconfigured (e.g. 0) window doesn't feed Whisper an empty array.
        window_frame_count = max(1, round(self.PARTIAL_WINDOW_S / FRAME_DURATION_S))
        windowed_frames = self.segment_frames[-window_frame_count:]
        audio_np = np.concatenate(windowed_frames)
        result = await asyncio.to_thread(get_engine().transcribe, audio_np)
        if result["text"]:
            await self.send(text_data=json.dumps({"type": "partial", "text": result["text"]}))

    async def _finalize_segment(self):
        from stt.engine import get_engine

        if not self.speech_active or not self.segment_frames:
            self.speech_active = False
            return

        # Trim the silence tail that was accumulated while waiting for
        # SILENCE_S to elapse — feeding trailing silence to Whisper degrades
        # accuracy and adds unnecessary latency.
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

        result = await asyncio.to_thread(get_engine().transcribe, audio_np)
        text = result["text"]
        if not text:
            self.gap_since_last_turn_s = 0.0
            return

        if speaker_module.diarization_enabled():
            try:
                speaker_label = await asyncio.to_thread(
                    speaker_module.get_diarizer().label_segment, audio_np, SAMPLE_RATE
                )
            except Exception:
                # Diarization is opt-in and can fail for all sorts of
                # environment reasons (missing HF auth, model download,
                # etc.) — fall back rather than dropping the transcript.
                speaker_label = self.speaker_labeler.label(segment_gap_s)
        else:
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
        Phase 2: run the rule-based extractor on the just-finalized turn and
        persist/stream anything it finds. Runs after the `final` message so
        a slow or failing extraction never delays the transcript itself —
        extraction is best-effort on top of a working transcript, not a
        blocker for one.
        """
        try:
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
            Extraction(session=self.session, turn=turn, type=item.type, text=item.text, confidence=item.confidence)
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

        # Phase 3: fire off (don't await) suggestion generation for each
        # detected question. See the note on self._background_tasks in
        # connect() for why this is a background task rather than inline.
        for extraction_obj, item in zip(extraction_objs, found):
            if item.type == intent_module.QUESTION:
                task = asyncio.create_task(self._generate_suggestion(turn, extraction_obj))
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

    async def _generate_suggestion(self, turn: Turn, extraction: Extraction):
        """
        Phase 3 (v4): single-shot suggestion for one detected question,
        via the hybrid router — Groq first (ai.cloud_router), local LLM
        fallback. Runs as a background task (see connect()/
        _extract_and_emit) so it never blocks incoming audio frames on
        this connection. Best-effort: a missing/misconfigured Groq key
        and local model (ai.local_llm.get_llm() raises FileNotFoundError
        until LLM_MODEL_PATH is set — see .env.example) or any inference
        failure just means no suggestion, not a crashed session.

        Diagnostic prints throughout (not just logger calls) are
        deliberate: this project has no LOGGING config in settings.py, so
        logger.info/debug calls have nowhere configured to go, and even
        logger.warning/exception only reach the console via Python's
        bare-minimum "last resort" stderr handler — easy to miss and easy
        to mistake for "nothing happened" when actually something did.
        print(..., flush=True) is the same pattern the startup warmup
        messages already use, and those are confirmed to show up.
        """
        from ai.cloud_router import get_suggestion

        # Snapshot the level at the moment generation starts, not when it
        # finishes — if the client switches levels mid-generation, this
        # in-flight suggestion should still reflect what was asked for
        # when it kicked off, not whatever the level happens to be later.
        level = self.answer_level

        print(
            f"[suggest] extraction {extraction.id} ({extraction.text!r}): "
            f"starting (level={level})",
            flush=True,
        )

        context_limit = int(os.environ.get("SUGGESTION_CONTEXT_TURNS", "6"))

        started = time.monotonic()
        try:
            context_turns = await self._recent_turns_context(context_limit)
            result = await asyncio.to_thread(
                get_suggestion, extraction.text, context_turns, level
            )
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
            f"{result.get('model')} in {latency_ms:.0f}ms",
            flush=True,
        )

        suggestion = await Suggestion.objects.acreate(
            session=self.session,
            turn=turn,
            extraction=extraction,
            question_text=extraction.text,
            answer_text=result["text"],
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
                        "question": extraction.text,
                        "answer": result["text"],
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
                        "extraction_confidence": extraction.confidence,
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
