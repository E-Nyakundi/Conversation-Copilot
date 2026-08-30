import uuid

from django.db import models


class Document(models.Model):
    """
    Phase 6 (2026-08-24): a piece of the user's personal knowledge base —
    plain text/notes only, per Ed's explicit scope call (skip PDF parsing
    for this pass; see build plan). Uploaded via
    knowledge/rest_views.py's upload endpoint, chunked and embedded
    (knowledge/documents.py orchestrates all three), then retrieved into
    ai/prompts.py's suggestion prompts at generation time.

    KNOWN LIMITATION (2026-08-30 multi-tenancy retrofit): still global,
    NOT scoped per tenant. UserProfile and Session were retrofitted with
    an `owner` FK in this same change; Document/Chunk deliberately were
    not, because the underlying FAISS index (knowledge/vector_store.py)
    is a single global on-disk index, not one per user — adding `owner`
    here without also splitting or filtering that index would only
    silently isolate the metadata while every tenant's documents remain
    retrievable through the same shared vector search. That's a bigger
    change (embeddings/retrieval/index-format, not just a model field)
    and is explicitly deferred; see the design doc. Until it lands, do
    NOT expose /api/knowledge/ to non-owner tenants — treat the
    knowledge base as owner-only in any multi-tenant deployment.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255, blank=True)
    # Raw text as submitted — kept in full even though it's also split
    # into Chunks below, so re-chunking/re-embedding (e.g. after a
    # chunking-strategy change) never requires the original upload again.
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title or f"Document {self.id}"


class Chunk(models.Model):
    """
    One chunked, embedded piece of a Document. `vector_index` is this
    chunk's position in knowledge/vector_store.py's FAISS index — the
    index itself only stores vectors + integer ids, not text, so this
    row is how a FAISS search hit gets back to readable content (see
    knowledge/documents.py:retrieve_context).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="chunks")
    text = models.TextField()
    order = models.PositiveIntegerField(help_text="Position of this chunk within its document, 0-indexed.")
    # The row id FAISS returns from a search — assigned when this chunk
    # is added to the vector store (see vector_store.py:add). Nullable so
    # a Chunk can exist mid-ingestion (row written) before its embedding/
    # index-add step completes; documents.py's ingest() only considers a
    # Document fully indexed once every Chunk has one set.
    vector_index = models.IntegerField(null=True, blank=True, unique=True)

    class Meta:
        ordering = ["document_id", "order"]

    def __str__(self):
        return f"{self.document_id} chunk {self.order}"
