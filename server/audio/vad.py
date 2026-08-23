"""
Silero VAD wrapper.

Uses the `silero-vad` PyPI package (not torch.hub). The ONNX model file
ships inside the pip package itself, so loading it never makes a network
call to GitHub at runtime — this avoids the GitHub anonymous-API rate
limit (60 req/hr/IP) that torch.hub.load() was hitting, especially after
repeated container rebuilds/restarts from the same IP.

Like stt/engine.py, the import is deferred into get_vad() so importing
this module doesn't require torch/onnxruntime to be installed just to run
`manage.py check`, admin, REST endpoints, etc.

Silero VAD (v4/v5) expects fixed-size frames for a given sample rate:
512 samples (32ms) at 16kHz is the recommended default and what the rest
of this pipeline assumes (see FRAME_SAMPLES below).
"""

import logging
import os
import threading

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_SAMPLES = 512  # ~32ms at 16kHz — required chunk size for silero-vad

_vad = None
_vad_lock = threading.Lock()


class SileroVAD:
    def __init__(self):
        import torch  # deferred import, see module docstring

        self.torch = torch
        try:
            from silero_vad import load_silero_vad

            # onnx=True: no torch.hub, no GitHub call — the model file is
            # bundled inside the silero-vad pip package.
            self.model = load_silero_vad(onnx=True)
        except Exception as e:
            # Belt-and-braces: if the pip package/model somehow fails to
            # load (bad wheel, corrupted install, etc.), fall back rather
            # than crashing the whole consumer. Callers get 1.0 (assume
            # speech) — see the note on speech_prob() about what that
            # means for the pipeline if VAD is genuinely unavailable.
            logger.error(
                f"Failed to load Silero VAD model: {e}. "
                "VAD will not be available for this session."
            )
            self.model = None

    def speech_prob(self, frame: np.ndarray) -> float:
        """
        frame: float32 numpy array, exactly FRAME_SAMPLES long, values in
        [-1, 1]. Returns speech probability in [0, 1].
        """
        if self.model is None:
            # VAD not available; default to True (assume speech). Note:
            # this means silence is NEVER detected upstream, so segments
            # will only finalize on an explicit end_session, not
            # automatically after a pause — see consumers.py.
            return 1.0
        if frame.shape[0] != FRAME_SAMPLES:
            raise ValueError(
                f"silero-vad expects exactly {FRAME_SAMPLES} samples per "
                f"frame at {SAMPLE_RATE}Hz, got {frame.shape[0]}"
            )
        with self.torch.no_grad():
            tensor = self.torch.from_numpy(frame)
            prob = self.model(tensor, SAMPLE_RATE).item()
        return prob


def get_vad() -> SileroVAD:
    global _vad
    if _vad is None:
        with _vad_lock:
            if _vad is None:
                _vad = SileroVAD()
    return _vad


def is_speech(frame: np.ndarray, threshold: float | None = None) -> bool:
    threshold = threshold if threshold is not None else float(
        os.environ.get("VAD_THRESHOLD", "0.5")
    )
    return get_vad().speech_prob(frame) >= threshold


def speech_prob(frame: np.ndarray) -> float:
    """
    Phase C (2026-08-23): thin passthrough so callers that need the raw
    probability (not just the thresholded bool from is_speech()) don't
    have to reach into get_vad() directly. Added for
    consumers.py's adaptive-silence heuristic — see that module's
    _process_frame for how this is used alongside RMS-decay to judge
    whether a pause looks like a real sentence end or a mid-thought
    breath, rather than treating every silence identically.
    """
    return get_vad().speech_prob(frame)
