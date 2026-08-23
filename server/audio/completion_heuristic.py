"""
Phase C (2026-08-23): adaptive turn-finalization heuristic.

Problem this addresses (build plan §11a / Ed's "premature answering"
report): AudioStreamConsumer used to finalize a turn after a single fixed
silence threshold (VAD_SILENCE_MS, 600ms default) with zero regard for
*what kind* of pause it was. A person taking a breath mid-sentence, or
pausing to think before continuing a long system-design answer, produces
the exact same "600ms of silence" signal as someone who has genuinely
finished speaking — so the pipeline would finalize, transcribe, extract,
and generate a suggestion mid-thought, which is exactly what "premature
answering" looks like from the user's side. This is the same root cause
as the VAD-chunking bug already logged in §11a (long prompts getting cut
into multiple turns) — different symptom, same fixed-threshold problem.

Ed's decision (2026-08-23): don't pick one fix, combine all three:
  1. Raise the floor (VAD_SILENCE_MS default 600 -> 1000).
  2. Add a punctuation/prosody-style heuristic.
  3. Make the effective threshold adaptive per-turn.

On (2): real punctuation isn't available at this stage of the pipeline —
STT (which is what would produce actual punctuation/sentence-boundary
info) only runs *after* a segment is finalized, not before, so waiting
for a transcript to decide whether to finalize would be circular. The
practical, real-time-available proxy used instead is trailing RMS
(volume) decay, combined with the VAD model's own trailing speech-
probability trend (already computed every frame for the is_speech()
call, just never retained). Both are legitimate, well-established
prosodic completion cues in speech processing — a genuinely finished
utterance tends to trail off in loudness and confidence rather than
cutting off sharply, the way a breath-pause or a thinking-pause usually
does (energy/confidence stay comparatively higher right up to the cut,
because the speaker didn't intend to stop). This is a real, if imperfect
and unvalidated-on-real-audio, signal — not decorative window dressing;
see the module-level "Not yet done" note at the bottom for the honest
caveat on that.

On (3): the adaptive threshold blends this completion score with the
raw silence duration — a high completion score lets a turn finalize
close to the (raised) floor, a low completion score extends the
effective wait, up to a hard ceiling so a persistently ambiguous trailing
pause still finalizes eventually rather than hanging forever.
"""

import os

import numpy as np

# Config (env-overridable — see server/.env.example). Renamed the
# underlying default from 600 to 1000 per Ed's explicit decision to
# raise the floor; VAD_SILENCE_MS itself (read in consumers.py) now
# represents this floor, not a fixed cutoff.
MIN_SILENCE_S = float(os.environ.get("VAD_SILENCE_MS", "1000")) / 1000.0

# Ceiling on the *adaptive* extension above the floor — without this, a
# trailing pause that never looks "complete" (e.g. background noise
# keeping RMS/speech-prob from ever trailing off) would never finalize
# at all. This bounds the worst case to floor + ceiling, not unbounded.
MAX_ADAPTIVE_EXTENSION_S = float(os.environ.get("VAD_ADAPTIVE_MAX_EXTENSION_MS", "1200")) / 1000.0

# How many trailing frames (most recent silence) to look at when judging
# the completion trend. ~500ms worth at the 32ms/frame Silero uses.
TREND_WINDOW_FRAMES = int(os.environ.get("VAD_TREND_WINDOW_FRAMES", "16"))


def _rms(frame: np.ndarray) -> float:
    """Root-mean-square amplitude of a float32 frame in [-1, 1]."""
    if frame.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(frame))))


class CompletionTracker:
    """
    Accumulates per-frame (speech_prob, rms) pairs for the tail end of a
    segment (the frames spanning the trailing silence, plus a little
    speech immediately before it) and estimates whether the pause looks
    like a genuine end-of-utterance or a mid-thought pause.

    One instance per in-progress segment — created when speech starts,
    fed a sample every frame, read via completion_score() /
    effective_silence_threshold() while waiting out the silence tail,
    and discarded when the segment finalizes or a new one starts.
    """

    def __init__(self):
        self._probs: list[float] = []
        self._rms: list[float] = []

    def add_frame(self, frame: np.ndarray, speech_prob: float):
        self._probs.append(speech_prob)
        self._rms.append(_rms(frame))
        # Only the trailing window matters for the trend — an hour-long
        # segment shouldn't make this list grow unbounded, and older
        # frames have no bearing on "does *this* pause look complete."
        if len(self._probs) > TREND_WINDOW_FRAMES * 3:
            self._probs = self._probs[-TREND_WINDOW_FRAMES * 3 :]
            self._rms = self._rms[-TREND_WINDOW_FRAMES * 3 :]

    def completion_score(self) -> float:
        """
        Returns a score in [0, 1]: how much the trailing audio looks like
        a genuine sentence/thought completion rather than a mid-utterance
        pause. Higher = more confidently "done" = shorter effective wait
        is justified.

        Two components, averaged:
          - RMS decay: compares the last third of the trend window
            against the first third. A real trail-off (last quieter than
            first) scores high; flat or rising energy scores low.
          - Speech-prob decay: same comparison using Silero's own
            speech-probability trace, which tends to fall off ahead of a
            genuine stop and stay more ambiguous through a breath pause.

        With fewer than a handful of frames to work with (e.g. right at
        segment start), returns a neutral 0.5 — not enough signal yet to
        push the adaptive threshold either direction, so it behaves like
        the plain fixed-floor case until real trend data accumulates.
        """
        n = len(self._probs)
        if n < 6:
            return 0.5

        window = min(n, TREND_WINDOW_FRAMES)
        probs = self._probs[-window:]
        rms = self._rms[-window:]
        third = max(1, window // 3)

        prob_early = sum(probs[:third]) / third
        prob_late = sum(probs[-third:]) / third
        rms_early = sum(rms[:third]) / third
        rms_late = sum(rms[-third:]) / third

        # Normalize each decay into roughly [0, 1] where 1.0 = strong
        # decay (early value at least double the late value or more),
        # 0.0 = flat/rising. Guarded against division by ~zero.
        def decay_score(early: float, late: float) -> float:
            if early <= 1e-6:
                return 0.5  # nothing to compare against, stay neutral
            ratio = late / early
            # ratio near 0 (fully decayed) -> score near 1
            # ratio >= 1 (flat/rising) -> score 0
            return max(0.0, min(1.0, 1.0 - ratio))

        prob_decay = decay_score(prob_early, prob_late)
        rms_decay = decay_score(rms_early, rms_late)
        return (prob_decay + rms_decay) / 2.0

    def effective_silence_threshold(self) -> float:
        """
        Blends completion_score() into MIN_SILENCE_S: a high score
        (confidently complete) finalizes right at the floor; a low score
        (looks mid-thought) extends the wait, up to
        MIN_SILENCE_S + MAX_ADAPTIVE_EXTENSION_S.
        """
        score = self.completion_score()
        extension = (1.0 - score) * MAX_ADAPTIVE_EXTENSION_S
        return MIN_SILENCE_S + extension


# Not yet done / not verified: like every other Phase C-adjacent item in
# the build plan, the decay-score weighting (simple average, thirds-based
# early/late comparison) and the extension ceiling are informed
# estimates, not tuned against real recorded pause/completion audio.
# Next real step once this is deployed: capture real session audio around
# both correctly- and incorrectly-finalized turns and check whether
# completion_score() actually tracks the difference, the same
# eyeball-verification posture already used for score_extraction.py and
# score_answer_format.py.
