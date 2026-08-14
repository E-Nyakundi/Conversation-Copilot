"""
Speaker separation, Phase 1 originally ("basic speaker separation,
2-speaker case first" per the build plan), revisited 2026-08-14 to go
session-persistent (build-plan priority 1 as of that date).

Three implementations, selected by SPEAKER_DIARIZATION_MODE (or the
legacy ENABLE_DIARIZATION flag — see diarization_mode() below):

1. NaivePauseSpeakerLabeler ("naive") — flips between "A"/"B" when the
   gap since the previous finalized turn exceeds a threshold, otherwise
   assumes the same speaker continued. A real heuristic, not a
   placeholder — but wrong on quick back-and-forth with no pause, and
   wrong on solo monologue with pauses (it'll flip when it shouldn't).
   Always kept alive as the per-turn fallback for the other two modes,
   not just a standalone mode of its own.

2. PyannoteEmbedder + SessionSpeakerRegistry ("embedding", **default as
   of 2026-08-14**) — real diarization with actual cross-turn speaker
   identity. Runs only the pyannote embedding model (no
   segmentation/clustering) on each already-VAD-segmented turn to get
   one d-vector, then matches it against a per-session registry of
   running speaker centroids via cosine similarity. This is the
   session-persistent upgrade the naive labeler and (1)'s sibling below
   were both missing: it remembers that "Speaker A" three turns ago is
   the same Speaker A now, rather than re-deciding per segment or per
   pause. Deliberately lighter than running the full diarization
   pipeline per turn (see PyannoteSpeakerLabeler below) — the build plan
   flagged this exact fallback ("a lighter dedicated embedding model...
   if running the full diarization pipeline on every turn proves too
   slow") as the lever to pull if needed, and it's pulled from the
   start here given this VM's 2 vCPU/5.8GB RAM ceiling is already
   tight (see build plan §6a, and Whisper's own medium/large OOM
   history). Requires the same pyannote.audio + HF_TOKEN setup as (3).

3. PyannoteSpeakerLabeler ("pyannote_full") — the original Phase 1
   opt-in: the full pyannote.audio diarization pipeline
   (VAD+segmentation+clustering) run independently per segment, so it's
   really "who's the dominant voice in *this* segment" with no memory
   across turns. Kept, not removed — this is the explicit fallback mode
   the build plan calls for if (2) proves too expensive live: "fall
   back to segment-only diarization... rather than blocking the whole
   feature on it."

Both ML modes are wrapped in try/except at the call site
(audio/consumers.py._finalize_segment) and fall back to the naive
pause labeler for that turn on any failure or timeout, per the build
plan: "if diarization fails or times out for a segment, fall back to
the naive pause labeler for that turn rather than dropping the speaker
label entirely."
"""

import os
import threading

import numpy as np

_diarizer = None
_diarizer_lock = threading.Lock()

_embedder = None
_embedder_lock = threading.Lock()


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


class SessionSpeakerRegistry:
    """Per-session (per WS connection) registry of speaker centroids.

    Stateful per-connection, same lifetime/scope as NaivePauseSpeakerLabeler
    already had — one instance created in AudioStreamConsumer.connect(),
    never shared across connections or sessions. Not persisted to the DB;
    a fresh session starts with an empty registry, which is correct
    (speaker identity here means "consistent within this conversation",
    not "recognizes this specific person globally").

    Call `assign(embedding)` once per finalized turn's speaker embedding.
    It returns a label ("A", "B", "C", ...), matching against existing
    centroids by cosine similarity and creating a new speaker only when
    nothing already known is close enough.
    """

    def __init__(self, similarity_threshold: float | None = None, max_speakers: int | None = None):
        self.threshold = similarity_threshold if similarity_threshold is not None else float(
            os.environ.get("SPEAKER_SIMILARITY_THRESHOLD", "0.72")
        )
        # Cap rather than let noisy embeddings spawn unbounded new
        # speakers — 2-speaker conversation is still the primary case to
        # validate (per build plan), this just avoids a pathological
        # runaway on a bad session rather than assuming exactly 2.
        self.max_speakers = max_speakers if max_speakers is not None else int(
            os.environ.get("SPEAKER_MAX_COUNT", "6")
        )
        self._centroids: dict[str, np.ndarray] = {}
        self._counts: dict[str, int] = {}
        self._next_index = 0

    def _new_label(self) -> str:
        label = chr(ord("A") + self._next_index) if self._next_index < 26 else f"SPEAKER_{self._next_index}"
        self._next_index += 1
        return label

    def assign(self, embedding: np.ndarray) -> str:
        norm = np.linalg.norm(embedding)
        embedding = embedding / norm if norm > 1e-8 else embedding

        if not self._centroids:
            label = self._new_label()
            self._centroids[label] = embedding
            self._counts[label] = 1
            return label

        best_label, best_sim = None, -1.0
        for label, centroid in self._centroids.items():
            sim = float(np.dot(embedding, centroid))
            if sim > best_sim:
                best_label, best_sim = label, sim

        if best_sim >= self.threshold or len(self._centroids) >= self.max_speakers:
            # Match — or we've hit the speaker cap, so fold this segment
            # into the closest known speaker rather than growing forever
            # on noise/mismatches.
            label = best_label
            n = self._counts[label]
            updated = (self._centroids[label] * n + embedding) / (n + 1)
            update_norm = np.linalg.norm(updated)
            self._centroids[label] = updated / update_norm if update_norm > 1e-8 else updated
            self._counts[label] = n + 1
            return label

        label = self._new_label()
        self._centroids[label] = embedding
        self._counts[label] = 1
        return label


class PyannoteEmbedder:
    """Runs only pyannote's speaker-embedding model (no segmentation or
    clustering) on a single already-VAD-segmented turn, returning one
    d-vector. Meant to pair with SessionSpeakerRegistry above — this
    class has no notion of "who this speaker is across turns" itself,
    that's the registry's job.

    Deliberately lighter-weight per-call than PyannoteSpeakerLabeler's
    full diarization pipeline, since a turn here is already a single
    pre-segmented chunk of one (mostly) continuous speaker — running full
    diarization on it is redoing work VAD already did.
    """

    def __init__(self):
        from pyannote.audio import Inference, Model  # deferred import — heavy optional dep

        hf_token = os.environ.get("HF_TOKEN") or None
        model = Model.from_pretrained("pyannote/embedding", use_auth_token=hf_token)
        # window="whole" -> one embedding for the entire given audio,
        # instead of pyannote's default sliding-window multi-embedding
        # output (which is meant for long, multi-speaker recordings we
        # don't have here — our input is already one finalized turn).
        self.inference = Inference(model, window="whole")

    def embed(self, audio: np.ndarray, sample_rate: int = 16000) -> np.ndarray | None:
        import torch

        if audio.size == 0:
            return None

        waveform = torch.from_numpy(audio).float().unsqueeze(0)  # (1, n_samples)
        embedding = self.inference({"waveform": waveform, "sample_rate": sample_rate})
        return np.asarray(embedding).reshape(-1)


class PyannoteSpeakerLabeler:
    """Original Phase 1 opt-in path — full pipeline, segment-only, no
    cross-turn identity. Kept as the "pyannote_full" fallback mode."""

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


def get_embedder() -> "PyannoteEmbedder":
    global _embedder
    if _embedder is None:
        with _embedder_lock:
            if _embedder is None:
                _embedder = PyannoteEmbedder()
    return _embedder


def diarization_mode() -> str:
    """Which speaker-labeling backend _finalize_segment should use.

    - "embedding" (new default, 2026-08-14): SessionSpeakerRegistry +
      PyannoteEmbedder, real cross-turn speaker identity.
    - "pyannote_full": the original Phase 1 opt-in — full pipeline,
      segment-only, no cross-turn identity. Explicit fallback mode if
      "embedding" proves too expensive on this VM.
    - "naive": disables ML-based diarization entirely, pure
      NaivePauseSpeakerLabeler gap-flipping (the pre-2026-08-14 default
      behavior).

    Backward-compatible with the old ENABLE_DIARIZATION=0/1 flag so
    existing .env files don't silently change behavior on deploy:
    explicitly setting SPEAKER_DIARIZATION_MODE always wins; otherwise
    ENABLE_DIARIZATION=0 maps to "naive" (the old default) and
    ENABLE_DIARIZATION=1 maps to "pyannote_full" (the old opt-in). Only
    when *neither* var is set does this resolve to the new "embedding"
    default — so picking up the new default on the VM requires actually
    updating server/.env.production, not just deploying this code (see
    build plan note).
    """
    explicit = os.environ.get("SPEAKER_DIARIZATION_MODE", "").strip().lower()
    if explicit in ("embedding", "pyannote_full", "naive"):
        return explicit

    legacy = os.environ.get("ENABLE_DIARIZATION")
    if legacy == "0":
        return "naive"
    if legacy == "1":
        return "pyannote_full"

    return "embedding"


def diarization_enabled() -> bool:
    """Deprecated — kept only in case anything outside consumers.py still
    imports it. Use diarization_mode() != "naive" instead."""
    return diarization_mode() != "naive"