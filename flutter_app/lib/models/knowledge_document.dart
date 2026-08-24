/// Phase 6 (2026-08-24): a document in the user's personal knowledge base.
/// Mirrors knowledge/rest_views.py's `_document_summary` shape exactly —
/// text-only for this pass (no PDF parsing yet, see knowledge/models.py's
/// own docstring for that scope call).
class KnowledgeDocument {
  const KnowledgeDocument({
    required this.id,
    required this.title,
    required this.createdAt,
    required this.chunkCount,
    required this.textLength,
    this.text,
    this.chunks,
  });

  final String id;
  final String title;
  final DateTime? createdAt;
  final int chunkCount;
  final int textLength;

  /// Only populated by [KnowledgeRepository.fetchDocument] (the detail
  /// call) — the list endpoint intentionally omits full text to keep the
  /// list call cheap, same reasoning session_repository.dart already
  /// uses for session summaries vs. session detail.
  final String? text;
  final List<KnowledgeChunk>? chunks;

  /// Indexing (chunking + embedding + FAISS add) runs synchronously
  /// inside the upload request per rest_views.py's docstring, so a
  /// document that exists at all is already fully indexed — there's no
  /// separate "indexing..." state to show. chunkCount > 0 here just
  /// means "has content", not "still processing".
  bool get isIndexed => chunkCount > 0;
}

class KnowledgeChunk {
  const KnowledgeChunk({required this.order, required this.text});

  final int order;
  final String text;
}
