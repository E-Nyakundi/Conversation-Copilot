"""
Speaker separation, Phase 1 ("basic speaker separation, 2-speaker case
first" per the build plan).

Two implementations, selected by the ENABLE_DIARIZATION env var:

1. NaivePauseSpeakerLabeler (default, no extra deps/models needed) —
   flips between "A"/"B" when the gap since the previous finalized turn
   exceeds a threshold, and otherwise assumes the same speaker continued.
   This is a real heuristic, not a placeholder that always says
   "unknown" — but it WILL get it wrong on quick back-and-forth exchanges
   with no pause, and on solo monologue with pauses (it'll wrongly flip).
   Good enough to unblock Phase 1 end-to-end; revisit before Phase 8
   (debrief) depends on speaker accuracy.

2. PyannoteSpeakerLabeler (opt-in) — real diarization via
   pyannote.audio's pretrained pipeline. Requires:
     - `pip install pyannote.audio` (uncomment in requirements.txt)
     - a Hugging Face token with access to
       pyannote/speaker-diarization-3.1 (accept the model's terms on HF,
       then set HF_TOKEN in server/.env)
     - ENABLE_DIARIZATION=1 in server/.env
   This runs on each finalized segment independently (not the whole
   session), so it's really "who's the dominant voice in this segment"
   rather than a session-wide diarization with consistent speaker
   identity across turns — that upgrade (embedding-based speaker
   clustering across the whole session) is a reasonable Phase 2/5 target
   once Phase 1 proves the pipe is clean.
"""

import os
import threading

import numpy as np

_diarizer = None
_diarizer_lock = threading.Lock()


class NaivePauseSpeakerLabeler:
    """Stateful per-connection. Call `label(gap_seconds)` each time a turn
    finalizes; it decides A vs B based on the silence gap before it."""

    def __init__(self, flip_gap_seconds: float | None = None):
        self.flip_gap_seconds = flip_gap_seconds if flip_gap_seconds is not None else float(
            os.environ.get("SPEAKER_FLIP_GAP_SECONDS", "0.7")
        )
        self._current = "A"

    def label(self, gap_seconds: float) -> str:
        current = self._current
        if gap_seconds >= self.flip_gap_seconds:
            self._current = "B" if self._current == "A" else "A"
        return current


class PyannoteSpeakerLabeler:
    def __init__(self):
        from pyannote.audio import Pipeline  # deferred import — heavy optional dep

        hf_token = os.environ.get("HF_TOKEN") or None
        self.pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1", use_auth_token=hf_token
        )

    def label_segment(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        """Returns the dominant speaker label (e.g. 'SPEAKER_00') within
        this segment's audio. Falls back to 'unknown' if the segment is
        too short/quiet for the pipeline to produce a result."""
        import torch

        if audio.size == 0:
            return "unknown"

        waveform = torch.from_numpy(audio).unsqueeze(0)  # (1, n_samples)
        diarization = self.pipeline({"waveform": waveform, "sample_rate": sample_rate})

        durations: dict[str, float] = {}
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            durations[speaker] = durations.get(speaker, 0.0) + turn.duration

        if not durations:
            return "unknown"
        return max(durations, key=durations.get)


def get_diarizer() -> "PyannoteSpeakerLabeler":
    global _diarizer
    if _diarizer is None:
        with _diarizer_lock:
            if _diarizer is None:
                _diarizer = PyannoteSpeakerLabeler()
    return _diarizer


def diarization_enabled() -> bool:
    return os.environ.get("ENABLE_DIARIZATION", "0") == "1"
