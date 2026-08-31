import uuid

from django.conf import settings
from django.db import models


class Document(models.Model):
    """
    Phase 6 (2026-08-24): a piece of the user's personal knowledge base —
    plain text/notes only, per Ed's explicit scope call (skip PDF parsing
    for this pass; see build plan). Uploaded via
    knowledge/rest_views.py's upload endpoint, chunked and embedded
    (knowledge/documents.py orchestrates all three), then retrieved into
    ai/prompts.py's suggestion prompts at generation time.

    Owner-scoping (2026-08-31, build plan §15): `owner` added so a
    tenant's own uploads (CV, LinkedIn/GitHub profile PDFs — see
    knowledge/pdf_extract.py) are attributable and filterable per-user.
    Nullable for the same reason Session/UserProfile's owner is nullable
    (see conversation/models.py's Session docstring) — makes the
    migration onto any pre-existing global documents possible without a
    forced backfill. The FAISS index itself is still one global index
    (see knowledge/vector_store.py) — this field alone does NOT isolate
    retrieval; knowledge/documents.py:retrieve_context is what actually
    filters search hits down to a given owner's Chunks. Do not treat
    this FK as sufficient isolation on its own.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="knowledge_documents",
        null=True,
    )
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
