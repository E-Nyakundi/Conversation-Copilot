"""
Phase 6: full rebuild of the FAISS index from the Document/Chunk rows
already in the DB — the recovery path for a lost/corrupted index file,
or for picking up a KNOWLEDGE_EMBEDDING_MODEL change (a different model
means a different vector dimension/space, so old vectors can't just be
kept — see vector_store.py:get_store's dimension note).

Deletes the on-disk index file and every Chunk's vector_index, then
re-embeds and re-adds every chunk from scratch. Does NOT re-chunk
Document.text — chunking is deterministic given the same
KNOWLEDGE_CHUNK_SIZE/OVERLAP, and a chunking-strategy change is a
separate, bigger operation (would also need re-creating Chunk rows, not
just re-indexing) left out of this command's scope.

Usage:
    python manage.py reindex_knowledge
    python manage.py reindex_knowledge --yes   # skip the confirmation prompt
"""

import os
from pathlib import Path

from django.core.management.base import BaseCommand

from knowledge.models import Chunk


class Command(BaseCommand):
    help = "Rebuild the FAISS knowledge-base index from scratch, from the DB's Chunk rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip the confirmation prompt (for non-interactive use, e.g. a deploy script).",
        )

    def handle(self, *args, **options):
        chunk_count = Chunk.objects.count()
        if chunk_count == 0:
            self.stdout.write("No chunks in the database — nothing to reindex.")
            return

        if not options["yes"]:
            confirm = input(
                f"This will delete the existing FAISS index and re-embed all "
                f"{chunk_count} chunk(s) from scratch. Continue? [y/N] "
            )
            if confirm.strip().lower() != "y":
                self.stdout.write("Aborted.")
                return

        from knowledge.vector_store import reset_store

        index_path = Path(os.environ.get("KNOWLEDGE_INDEX_PATH", "/data/knowledge_index.faiss"))
        if index_path.exists():
            index_path.unlink()
            self.stdout.write(f"Deleted existing index file at {index_path}.")

        Chunk.objects.update(vector_index=None)
        reset_store()

        # Re-import fresh after reset_store() so get_store() below builds
        # a brand-new (now-empty) index rather than reusing the stale
        # in-process singleton.
        from knowledge.embeddings import get_embedder
        from knowledge.vector_store import get_store

        embedder = get_embedder()
        store = get_store()

        chunks = list(Chunk.objects.all().order_by("document_id", "order"))
        texts = [c.text for c in chunks]
        vectors = embedder.embed(texts)

        # Reuses each chunk's original vector_index-assignment ordering
        # (0..N-1 across this rebuild) rather than knowledge.documents'
        # _next_vector_id counter, since every chunk is being re-added
        # together here — a fresh, contiguous id space is simplest and
        # correct for a full rebuild specifically.
        ids = list(range(len(chunks)))
        store.add(vectors, ids)

        for chunk, vector_id in zip(chunks, ids):
            chunk.vector_index = vector_id
        Chunk.objects.bulk_update(chunks, ["vector_index"])

        self.stdout.write(self.style.SUCCESS(f"Reindexed {len(chunks)} chunk(s)."))
