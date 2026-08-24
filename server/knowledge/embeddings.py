"""
Phase 6: sentence-transformers embedding wrapper, lazy singleton, same
pattern as stt/engine.py's WhisperEngine — model load is the expensive
part, so it happens once per process, not once per request. Import of
sentence_transformers itself is deferred into get_embedder() so that
`manage.py check`/`makemigrations` and the rest of the app don't
hard-require torch/sentence-transformers just to boot, same reasoning
stt/engine.py's own docstring gives.
"""

import os
import threading

_embedder = None
_embedder_lock = threading.Lock()


class Embedder:
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer  # deferred import, see module docstring

        print(f"[knowledge.embeddings] Loading embedding model '{model_name}'...", flush=True)
        self.model = SentenceTransformer(model_name)
        self.dim = self.model.get_sentence_embedding_dimension()
        print(f"[knowledge.embeddings] Embedding model ready (dim={self.dim}).", flush=True)

    def embed(self, texts: list[str]):
        """Returns a numpy array of shape (len(texts), self.dim), float32."""
        import numpy as np

        if not texts:
            return np.empty((0, self.dim), dtype="float32")
        vectors = self.model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
        return vectors.astype("float32")


def get_embedder() -> Embedder:
    """
    Thread-safe lazy singleton, same shape as stt/engine.py's
    get_engine() and ai/cloud_router.py's _get_groq_client(). Default
    model is a small, fast sentence-transformers model — good enough for
    short-note retrieval and cheap to run on the same 2 vCPU VM already
    hosting Whisper + the local LLM fallback (see build plan §6a's RAM
    budget); overridable via KNOWLEDGE_EMBEDDING_MODEL.
    """
    global _embedder
    if _embedder is None:
        with _embedder_lock:
            if _embedder is None:
                model_name = os.environ.get("KNOWLEDGE_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
                _embedder = Embedder(model_name)
    return _embedder
