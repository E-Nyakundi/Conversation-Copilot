"""
Phase 6: plain-text chunking for knowledge documents. No external deps —
just paragraph-aware splitting with a size cap, since scope is text/notes
only (no PDF structure, no headings to key off of).
"""

import os

# Character-based, not token-based — good enough for this app's purpose
# (retrieval context, not a tight token budget) and avoids pulling in a
# tokenizer dependency just for chunk sizing. Overridable via env for
# tuning once real usage shows whether these are too coarse/fine.
DEFAULT_CHUNK_SIZE = int(os.environ.get("KNOWLEDGE_CHUNK_SIZE", "800"))
DEFAULT_CHUNK_OVERLAP = int(os.environ.get("KNOWLEDGE_CHUNK_OVERLAP", "100"))


def chunk_text(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP) -> list[str]:
    """
    Splits on paragraph boundaries (blank lines) first, then packs
    paragraphs into chunks up to chunk_size characters, so a chunk
    doesn't cut a sentence in half when it can reasonably avoid it. A
    single paragraph longer than chunk_size is hard-split with overlap,
    since a chunk_size cap has to hold even for one huge paragraph.

    Empty/whitespace-only input returns an empty list — nothing to
    ingest, not a single empty-string chunk.
    """
    text = text.strip()
    if not text:
        return []

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        # No blank-line paragraph breaks at all (e.g. one long block of
        # text) — treat the whole thing as one paragraph to hard-split
        # below rather than silently dropping it.
        paragraphs = [text]

    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        if len(para) > chunk_size:
            # Flush whatever's pending before hard-splitting this
            # oversized paragraph on its own, so it doesn't get merged
            # into a chunk that's already near the cap.
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_hard_split(para, chunk_size, overlap))
            continue

        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = para

    if current:
        chunks.append(current)

    return chunks


def _hard_split(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Fixed-window split with overlap, for a paragraph too long to keep whole."""
    step = max(chunk_size - overlap, 1)
    pieces = []
    for start in range(0, len(text), step):
        piece = text[start : start + chunk_size]
        if piece:
            pieces.append(piece)
        if start + chunk_size >= len(text):
            break
    return pieces
