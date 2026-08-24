"""
Phase 6: ties knowledge/chunking.py, knowledge/embeddings.py, and
knowledge/vector_store.py together into the two operations the rest of
the app actually needs — ingest() (upload -> chunked, embedded, indexed
Document) and retrieve_context() (question -> relevant passages).
ai/prompts.py imports retrieve_context directly; it doesn't reach into
chunking/embeddings/vector_store itself, same separation of concerns
that module's own docstring already describes for who calls whom.
"""

import os

from django.db import transaction

from knowledge.chunking import chunk_text
from knowledge.models import Chunk, Document


def ingest(title: str, text: str) -> Document:
    """
    Creates a Document, chunks its text, embeds every chunk, and adds
    them to the FAISS index — end to end, synchronous (upload requests
    are not expected to be huge given the text/notes-only scope, so no
    background task is used here, unlike audio's suggestion generation).

    Chunk rows are written before embedding so a document is never
    silently missing from the DB even if embedding/indexing fails
    partway — but a Chunk with vector_index still null is not yet
    retrievable (see retrieve_context, which only ever queries FAISS,
    so an un-indexed Chunk simply can't be found — no separate "is this
    ready" check needed elsewhere).
    """
    from knowledge.embeddings import get_embedder
    from knowledge.vector_store import get_store

    document = Document.objects.create(title=title, text=text)

    pieces = chunk_text(text)
    if not pieces:
        return document

    with transaction.atomic():
        chunks = [Chunk.objects.create(document=document, text=piece, order=i) for i, piece in enumerate(pieces)]

    embedder = get_embedder()
    vectors = embedder.embed(pieces)

    store = get_store()
    # Chunk.id is a UUID (not an int), so it can't be used directly as a
    # FAISS id — vector_index is a separate PositiveIntegerField assigned
    # here from the chunk's own primary key row order combined with a
    # global counter, so ids stay unique across every Document ever
    # ingested, not just within this one.
    start_id = _next_vector_id()
    ids = list(range(start_id, start_id + len(chunks)))
    store.add(vectors, ids)

    for chunk, vector_id in zip(chunks, ids):
        chunk.vector_index = vector_id
        chunk.save(update_fields=["vector_index"])

    return document


def _next_vector_id() -> int:
    """
    FAISS ids need to be unique ints across the whole index's lifetime,
    not just within one ingest() call — highest existing vector_index + 1
    is a simple, correct-enough scheme for this app's scale (a personal
    knowledge base, not a multi-tenant system with concurrent ingests).
    """
    highest = Chunk.objects.exclude(vector_index=None).order_by("-vector_index").values_list(
        "vector_index", flat=True
    ).first()
    return (highest + 1) if highest is not None else 0


def retrieve_context(query: str, top_k: int | None = None) -> list[str]:
    """
    The one function ai/prompts.py actually calls (see that module's
    _build_knowledge_block / retrieve_knowledge_context). Embeds the
    query, searches the FAISS index, and returns the matching chunks'
    text — highest-relevance first — as a plain list of strings, no
    Chunk/Document objects leaked across the boundary.

    Same "absence means silently unchanged" posture as
    build_identity_context in ai/prompts.py: an empty knowledge base (no
    Documents ingested yet) or any failure here degrades to an empty
    list rather than raising, so a knowledge-base problem can never take
    down suggestion generation — same "silently-optional feature"
    reasoning ai/prompts.py:get_system_prompt already applies to
    UserProfile lookups.
    """
    if not query or not query.strip():
        return []

    try:
        from knowledge.embeddings import get_embedder
        from knowledge.vector_store import get_store

        top_k = top_k or int(os.environ.get("KNOWLEDGE_RETRIEVAL_TOP_K", "3"))
        min_score = float(os.environ.get("KNOWLEDGE_RETRIEVAL_MIN_SCORE", "0.3"))

        embedder = get_embedder()
        store = get_store()

        query_vector = embedder.embed([query])[0]
        hits = store.search(query_vector, top_k)

        vector_ids = [vid for vid, score in hits if score >= min_score]
        if not vector_ids:
            return []

        # Preserve FAISS's relevance ordering — the DB query below has no
        # inherent order matching `hits`, so results are re-sorted against
        # the original ranking rather than trusting queryset order.
        chunks_by_id = {c.vector_index: c.text for c in Chunk.objects.filter(vector_index__in=vector_ids)}
        return [chunks_by_id[vid] for vid in vector_ids if vid in chunks_by_id]
    except Exception:
        import logging

        logging.getLogger(__name__).warning("Knowledge retrieval failed, degrading to no context", exc_info=True)
        return []


def delete_document(document: Document) -> None:
    """Removes a Document's chunks from both the DB and the FAISS index — the counterpart to ingest()."""
    from knowledge.vector_store import get_store

    vector_ids = list(document.chunks.exclude(vector_index=None).values_list("vector_index", flat=True))
    if vector_ids:
        get_store().remove(vector_ids)
    document.delete()
