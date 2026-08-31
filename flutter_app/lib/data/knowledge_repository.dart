import 'dart:convert';

import 'package:http/http.dart' as http;

import '../api_config.dart';
import '../auth/auth_store.dart';
import '../models/knowledge_document.dart';

/// Client for knowledge/rest_views.py's tenant-facing
/// /api/knowledge/my-documents/ surface (Phase 6, 2026-08-24; PDF support
/// + owner-scoping added 2026-08-31, build plan §15). Same shape and
/// posture as SessionRepository: plain http.Client, ApiConfig.baseUrl for
/// the host, a KnowledgeRepositoryException on any non-2xx response,
/// dispose() to close the client.
///
/// Deliberately targets my-documents/ (owner-scoped, @token_required),
/// NOT documents/ (staff-only, unscoped — see rest_views.py's comments on
/// both). A regular tenant hitting documents/ would just get a 403; this
/// repository is what any tenant-facing screen (onboarding's upload step,
/// a future "manage your knowledge base" screen) should use instead.
class KnowledgeRepository {
  KnowledgeRepository({http.Client? client}) : _client = client ?? http.Client();

  final http.Client _client;

  Uri _documentsUri() => Uri.parse('${ApiConfig.baseUrl}/api/knowledge/my-documents/');
  Uri _documentUri(String id) =>
      Uri.parse('${ApiConfig.baseUrl}/api/knowledge/my-documents/$id/');

  Future<Map<String, String>> _authHeaders() async {
    final token = await AuthStore.instance.readToken();
    return {if (token != null) 'Authorization': 'Bearer $token'};
  }

  /// GET /api/knowledge/my-documents/ -> list the current user's own
  /// documents, newest first (server already orders by -created_at and
  /// caps at 100 — no pagination needed client-side for that ceiling).
  Future<List<KnowledgeDocument>> fetchDocuments() async {
    final response = await _client.get(_documentsUri(), headers: await _authHeaders());
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

  /// GET /api/knowledge/my-documents/`<id>`/ -> full text + ordered chunks.
  /// Separate from the list call for the same reason
  /// SessionRepository.fetchSessionDetail keeps detail separate from the
  /// list — full text/chunks aren't needed until a document is actually
  /// opened.
  Future<KnowledgeDocument> fetchDocument(String id) async {
    final response = await _client.get(_documentUri(id), headers: await _authHeaders());
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

  /// POST /api/knowledge/my-documents/ (JSON body) -> upload + synchronous
  /// chunk/embed/index (see knowledge/documents.py:ingest — the request
  /// doesn't return until indexing is done, so a 201 here means the
  /// document is immediately retrievable in the next suggestion, not
  /// "queued"). For pasted text/notes — for a PDF, use [uploadPdf] instead.
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
      headers: {'Content-Type': 'application/json', ...await _authHeaders()},
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

  /// Onboarding knowledge upload (2026-08-31, build plan §15): POST
  /// /api/knowledge/my-documents/ as multipart/form-data with a `file`
  /// field — a CV, a LinkedIn "Save to PDF" profile export, or a GitHub
  /// profile page printed to PDF (see server-side knowledge/pdf_extract.py
  /// for what's actually parsed out of it). Same synchronous
  /// chunk/embed/index behavior as [uploadDocument] — a 201 means the
  /// document is already retrievable, not queued for later processing.
  Future<KnowledgeDocument> uploadPdf({
    required List<int> bytes,
    required String filename,
    String title = '',
  }) async {
    final request = http.MultipartRequest('POST', _documentsUri());
    request.headers.addAll(await _authHeaders());
    if (title.trim().isNotEmpty) {
      request.fields['title'] = title.trim();
    }
    request.files.add(
      http.MultipartFile.fromBytes('file', bytes, filename: filename),
    );

    final streamed = await _client.send(request);
    final response = await http.Response.fromStream(streamed);
    if (response.statusCode != 201) {
      // The server returns {"error": "..."} for a 400 (e.g. a
      // password-protected or scanned/image-only PDF — see
      // pdf_extract.py:PdfExtractError) — surface that message when
      // present rather than just the status code, since it's actually
      // actionable ("this PDF has no extractable text") in a way a bare
      // "400" isn't.
      String message = 'Failed to upload PDF (${response.statusCode})';
      try {
        final body = jsonDecode(response.body) as Map<String, dynamic>;
        final serverError = body['error'] as String?;
        if (serverError != null && serverError.isNotEmpty) {
          message = serverError;
        }
      } catch (_) {
        // Non-JSON error body — fall back to the generic message above.
      }
      throw KnowledgeRepositoryException(message);
    }
    final body = jsonDecode(response.body) as Map<String, dynamic>;
    return _summaryFromJson(body);
  }

  /// DELETE /api/knowledge/my-documents/`<id>`/ -> removes DB rows + the
  /// document's vectors from the FAISS index (server-side, see
  /// knowledge/documents.py:delete_document — nothing extra needed here
  /// beyond the call itself).
  Future<void> deleteDocument(String id) async {
    final response = await _client.delete(_documentUri(id), headers: await _authHeaders());
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
