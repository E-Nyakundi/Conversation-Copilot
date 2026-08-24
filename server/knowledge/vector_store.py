"""
Phase 6: FAISS vector store wrapper, persisted to disk — same
volume-backed-persistence pattern already used for the sqlite DB (see
copilot/settings.py's SQLITE_DB_PATH comment): a plain file inside the
git-tracked source tree would risk the exact "table just vanished hours
in" class of bug a `git pull` caused there, so the index path is
configurable via env and expected to point at a dedicated volume in
Docker deployments (see docker-compose.yml / infra).

Lazy singleton, deferred faiss import — same shape as
knowledge/embeddings.py and stt/engine.py, for the same "don't
hard-require a heavy dependency just to boot Django" reason.
"""

import os
import threading
from pathlib import Path

_store = None
_store_lock = threading.Lock()


class VectorStore:
    def __init__(self, dim: int, index_path: Path):
        import faiss  # deferred import, see module docstring

        self._faiss = faiss
        self.dim = dim
        self.index_path = index_path
        self.index_path.parent.mkdir(parents=True, exist_ok=True)

        if self.index_path.exists():
            print(f"[knowledge.vector_store] Loading FAISS index from {self.index_path}...", flush=True)
            self.index = faiss.read_index(str(self.index_path))
        else:
            print(f"[knowledge.vector_store] No existing index at {self.index_path}, starting fresh.", flush=True)
            # IndexIDMap wraps a flat inner-product index so we can assign
            # our own ids (Chunk.vector_index) instead of relying on
            # FAISS's implicit sequential-position ids, which would break
            # the moment a chunk is ever deleted. Vectors are pre-
            # normalized by embeddings.py, so inner product == cosine
            # similarity here.
            self.index = faiss.IndexIDMap(faiss.IndexFlatIP(dim))

    def add(self, vectors, ids: list[int]):
        """vectors: numpy array (n, dim) float32. ids: list[int], same length."""
        import numpy as np

        if len(ids) == 0:
            return
        id_array = np.array(ids, dtype="int64")
        self.index.add_with_ids(vectors, id_array)
        self._save()

    def search(self, query_vector, top_k: int):
        """
        query_vector: numpy array (dim,) or (1, dim) float32.
        Returns list of (id, score) tuples, highest score first. Empty
        list if the index has nothing in it yet — a cold-start knowledge
        base isn't an error, retrieve_context() should just get nothing
        back.
        """
        import numpy as np

        if self.index.ntotal == 0:
            return []

        query_vector = np.asarray(query_vector, dtype="float32")
        if query_vector.ndim == 1:
            query_vector = query_vector.reshape(1, -1)

        top_k = min(top_k, self.index.ntotal)
        scores, ids = self.index.search(query_vector, top_k)
        results = []
        for idx, score in zip(ids[0], scores[0]):
            if idx == -1:
                continue
            results.append((int(idx), float(score)))
        return results

    def remove(self, ids: list[int]):
        """Removes chunks (e.g. on Document delete) so stale ids can't surface in future searches."""
        import numpy as np

        if not ids:
            return
        self.index.remove_ids(np.array(ids, dtype="int64"))
        self._save()

    def _save(self):
        self._faiss.write_index(self.index, str(self.index_path))


def get_store() -> VectorStore:
    """
    Thread-safe lazy singleton, same shape as knowledge/embeddings.py's
    get_embedder(). Dimension is taken from the embedder itself (not
    hardcoded) so a KNOWLEDGE_EMBEDDING_MODEL change that changes vector
    size doesn't silently mismatch — see the note in
    knowledge/documents.py:reindex_all for the one time that mismatch
    actually needs handling (a full rebuild).
    """
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                from knowledge.embeddings import get_embedder

                dim = get_embedder().dim
                default_path = "/data/knowledge_index.faiss"
                index_path = Path(os.environ.get("KNOWLEDGE_INDEX_PATH", default_path))
                _store = VectorStore(dim, index_path)
    return _store


def reset_store():
    """
    Drops the in-process singleton so the next get_store() call rebuilds
    from scratch — used by the reindex management command after deleting
    the on-disk index file, so a full rebuild doesn't append to a
    dimension-mismatched or stale in-memory index.
    """
    global _store
    with _store_lock:
        _store = None
