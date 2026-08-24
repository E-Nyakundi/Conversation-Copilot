import 'dart:convert';

import 'package:http/http.dart' as http;

import '../api_config.dart';
import '../models/knowledge_document.dart';

/// Client for knowledge/rest_views.py's /api/knowledge/documents/ surface
/// (Phase 6, 2026-08-24). Same shape and posture as SessionRepository:
/// plain http.Client, ApiConfig.baseUrl for the host, a
/// KnowledgeRepositoryException on any non-2xx response, dispose() to
/// close the client.
///
/// Text-only for this pass, matching the backend's explicit scope call
/// (knowledge/models.py:Document — "skip PDF parsing for this pass").
/// Upload is a title + a text body, nothing else.
class KnowledgeRepository {
  KnowledgeRepository({http.Client? client}) : _client = client ?? http.Client();

  final http.Client _client;

  Uri _documentsUri() => Uri.parse('${ApiConfig.baseUrl}/api/knowledge/documents/');
  Uri _documentUri(String id) =>
      Uri.parse('${ApiConfig.baseUrl}/api/knowledge/documents/$id/');

  /// GET /api/knowledge/documents/ -> list summaries, newest first
  /// (server already orders by -created_at and caps at 100 — no
  /// pagination needed client-side for that ceiling).
  Future<List<KnowledgeDocument>> fetchDocuments() async {
    final response = await _client.get(_documentsUri());
    if (response.statusCode != 200) {
      throw KnowledgeRepositoryException(
        'Failed to load knowledge base (${response.statusCode})',
      );
    }
    final body = jsonDecode(response.body) as Map<String, dynamic>;
    final documents = (body['documents'] as List<dynamic>? ?? [])
        .cast<Map<String, dynamic>>();
    return documents.map(_summaryFromJson).toList();
  }

  /// GET /api/knowledge/documents/<id>/ -> full text + ordered chunks.
  /// Separate from the list call for the same reason
  /// SessionRepository.fetchSessionDetail keeps detail separate from the
  /// list — full text/chunks aren't needed until a document is actually
  /// opened.
  Future<KnowledgeDocument> fetchDocument(String id) async {
    final response = await _client.get(_documentUri(id));
    if (response.statusCode == 404) {
      throw KnowledgeRepositoryException('Document not found');
    }
    if (response.statusCode != 200) {
      throw KnowledgeRepositoryException(
        'Failed to load document (${response.statusCode})',
      );
    }
    final body = jsonDecode(response.body) as Map<String, dynamic>;
    return _summaryFromJson(body, includeDetail: true);
  }

  /// POST /api/knowledge/documents/ -> upload + synchronous
  /// chunk/embed/index (see knowledge/documents.py:ingest — the request
  /// doesn't return until indexing is done, so a 201 here means the
  /// document is immediately retrievable in the next suggestion, not
  /// "queued").
  Future<KnowledgeDocument> uploadDocument({
    required String text,
    String title = '',
  }) async {
    final trimmed = text.trim();
    if (trimmed.isEmpty) {
      throw KnowledgeRepositoryException('Document text is required');
    }
    final response = await _client.post(
      _documentsUri(),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'title': title.trim(), 'text': trimmed}),
    );
    if (response.statusCode != 201) {
      throw KnowledgeRepositoryException(
        'Failed to upload document (${response.statusCode})',
      );
    }
    final body = jsonDecode(response.body) as Map<String, dynamic>;
    return _summaryFromJson(body);
  }

  /// DELETE /api/knowledge/documents/<id>/ -> removes DB rows + the
  /// document's vectors from the FAISS index (server-side, see
  /// knowledge/documents.py:delete_document — nothing extra needed here
  /// beyond the call itself).
  Future<void> deleteDocument(String id) async {
    final response = await _client.delete(_documentUri(id));
    if (response.statusCode != 200) {
      throw KnowledgeRepositoryException(
        'Failed to delete document (${response.statusCode})',
      );
    }
  }

  KnowledgeDocument _summaryFromJson(
    Map<String, dynamic> json, {
    bool includeDetail = false,
  }) {
    final title = (json['title'] as String?)?.trim();
    List<KnowledgeChunk>? chunks;
    if (includeDetail) {
      chunks = (json['chunks'] as List<dynamic>? ?? [])
          .cast<Map<String, dynamic>>()
          .map((c) => KnowledgeChunk(
                order: (c['order'] as num?)?.toInt() ?? 0,
                text: (c['text'] as String?) ?? '',
              ))
          .toList();
    }
    return KnowledgeDocument(
      id: (json['id'] as String?) ?? '',
      title: (title == null || title.isEmpty) ? 'Untitled document' : title,
      createdAt: DateTime.tryParse((json['created_at'] as String?) ?? ''),
      chunkCount: (json['chunk_count'] as num?)?.toInt() ?? 0,
      textLength: (json['text_length'] as num?)?.toInt() ?? 0,
      text: includeDetail ? (json['text'] as String?) : null,
      chunks: chunks,
    );
  }

  void dispose() {
    _client.close();
  }
}

class KnowledgeRepositoryException implements Exception {
  KnowledgeRepositoryException(this.message);
  final String message;

  @override
  String toString() => message;
}
