import 'dart:convert';

import 'package:http/http.dart' as http;

import '../api_config.dart';
import '../auth/auth_store.dart';
import '../models/session_summary.dart';

/// Real persistence layer (2026-08-24), replacing the SessionSummary model's
/// previous `mockSessions` constant as the actual data source for
/// Home/History/Session Detail. Talks to the REST endpoints already built
/// server-side in api/rest_views.py (GET /api/sessions/,
/// GET /api/sessions/<id>/, GET /api/sessions/<id>/suggestions/) — those
/// existed and were fully wired before this change; the gap being closed
/// here is purely that nothing in the app called them yet.
///
/// Two-request split for detail (turns + suggestions separately, then
/// merged client-side) matches what the server already exposes as two
/// endpoints rather than one combined one — no server change needed for
/// this first pass. If this ever becomes a real latency problem, the
/// natural fix is a combined endpoint server-side, not a Flutter-side
/// workaround.
class SessionRepository {
  SessionRepository({http.Client? client}) : _client = client ?? http.Client();

  final http.Client _client;

  /// GET /api/sessions/ -> list summaries for Home ("Recent Sessions") and
  /// Session History. Does NOT include transcript/suggestions — those are
  /// fetched separately per-session by [fetchSessionDetail] only when a
  /// session is actually opened, so the list call stays cheap regardless
  /// of how much a given session recorded.
  Future<List<SessionSummary>> fetchSessions() async {
    final uri = Uri.parse('${ApiConfig.baseUrl}/api/sessions/');
    final response = await _client.get(uri, headers: await _authHeaders());
    if (response.statusCode != 200) {
      throw SessionRepositoryException(
        'Failed to load sessions (${response.statusCode})',
      );
    }
    final body = jsonDecode(response.body) as Map<String, dynamic>;
    final sessions = (body['sessions'] as List<dynamic>? ?? [])
        .cast<Map<String, dynamic>>();
    return sessions.map(_summaryFromListJson).toList();
  }

  /// GET /api/sessions/<id>/ + GET /api/sessions/<id>/suggestions/ ->
  /// one SessionSummary with transcript populated (turns, each carrying
  /// its matching suggestion if the turn's question got one — matched by
  /// turn_id, same join the UI already expected from the old mock shape).
  Future<SessionSummary> fetchSessionDetail(String sessionId) async {
    final detailUri = Uri.parse('${ApiConfig.baseUrl}/api/sessions/$sessionId/');
    final suggestionsUri =
        Uri.parse('${ApiConfig.baseUrl}/api/sessions/$sessionId/suggestions/');

    final headers = await _authHeaders();
    final responses = await Future.wait([
      _client.get(detailUri, headers: headers),
      _client.get(suggestionsUri, headers: headers),
    ]);
    final detailResponse = responses[0];
    final suggestionsResponse = responses[1];

    if (detailResponse.statusCode == 404) {
      throw SessionRepositoryException('Session not found');
    }
    if (detailResponse.statusCode != 200) {
      throw SessionRepositoryException(
        'Failed to load session (${detailResponse.statusCode})',
      );
    }

    final detailBody = jsonDecode(detailResponse.body) as Map<String, dynamic>;

    // Suggestions are supplementary — a failure here shouldn't hide an
    // otherwise-successful transcript load, just means suggestion cards
    // won't show inline this time.
    final suggestionsByTurnId = <String, SuggestionRecord>{};
    if (suggestionsResponse.statusCode == 200) {
      final suggestionsBody =
          jsonDecode(suggestionsResponse.body) as Map<String, dynamic>;
      final suggestions = (suggestionsBody['suggestions'] as List<dynamic>? ?? [])
          .cast<Map<String, dynamic>>();
      for (final s in suggestions) {
        final turnId = s['turn_id'] as String?;
        if (turnId == null) continue;
        suggestionsByTurnId[turnId] = _suggestionFromJson(s);
      }
    }

    final turns = (detailBody['turns'] as List<dynamic>? ?? [])
        .cast<Map<String, dynamic>>();
    final transcript = turns.map((t) {
      final turnId = t['id'] as String?;
      return TranscriptLine(
        speaker: (t['speaker'] as String?) ?? 'unknown',
        text: (t['text'] as String?) ?? '',
        suggestion: turnId != null ? suggestionsByTurnId[turnId] : null,
      );
    }).toList();

    return _summaryFromListJson(detailBody, transcript: transcript);
  }

  SessionSummary _summaryFromListJson(
    Map<String, dynamic> json, {
    List<TranscriptLine> transcript = const [],
  }) {
    final startedAt = DateTime.tryParse((json['started_at'] as String?) ?? '');
    final endedAt = json['ended_at'] != null
        ? DateTime.tryParse(json['ended_at'] as String)
        : null;
    final turnCount = (json['turn_count'] as num?)?.toInt() ??
        transcript.length;
    final suggestionCount =
        transcript.where((l) => l.suggestion != null).length;
    final title = (json['title'] as String?)?.trim();

    return SessionSummary(
      id: (json['id'] as String?) ?? '',
      title: (title == null || title.isEmpty) ? 'Untitled session' : title,
      dateLabel: _dateLabel(startedAt),
      monthLabel: _monthLabel(startedAt),
      durationLabel: _durationLabel(startedAt, endedAt),
      hadSuggestions: suggestionCount > 0,
      suggestionCount: suggestionCount,
      turnCount: turnCount,
      transcript: transcript,
    );
  }

  SuggestionRecord _suggestionFromJson(Map<String, dynamic> json) {
    final extractionConfidence = json['extraction_confidence'] as num?;
    final latencyMs = json['latency_ms'] as num?;
    return SuggestionRecord(
      question: (json['question'] as String?) ?? '',
      answer: (json['answer'] as String?) ?? '',
      level: (json['answer_level'] as String?) ?? 'medium',
      latencyMs: latencyMs?.round(),
      confidencePct: extractionConfidence != null
          ? (extractionConfidence * 100).round()
          : null,
    );
  }

  static const _months = [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
  ];

  String _dateLabel(DateTime? started) {
    if (started == null) return 'Unknown date';
    final local = started.toLocal();
    final month = _months[local.month - 1].substring(0, 3);
    final hh = local.hour.toString().padLeft(2, '0');
    final mm = local.minute.toString().padLeft(2, '0');
    return '$month ${local.day} · $hh:$mm';
  }

  String _monthLabel(DateTime? started) {
    if (started == null) return 'Unknown';
    final local = started.toLocal();
    return '${_months[local.month - 1]} ${local.year}';
  }

  String _durationLabel(DateTime? started, DateTime? ended) {
    if (started == null || ended == null) return '—';
    final minutes = ended.difference(started).inMinutes;
    if (minutes < 1) return '<1m';
    return '${minutes}m';
  }

  /// Multi-tenancy retrofit (2026-08-30): every request now carries
  /// the bearer token (see accounts/auth.py's @token_required on the
  /// server side) — a missing token here means the server 401s and
  /// the response is surfaced as a normal SessionRepositoryException,
  /// same as any other failure status. AppRoot is what actually keeps
  /// this screen from being reachable while signed out, so this
  /// method doesn't special-case a null token itself.
  Future<Map<String, String>> _authHeaders() async {
    final token = await AuthStore.instance.readToken();
    return {if (token != null) 'Authorization': 'Bearer $token'};
  }

  void dispose() {
    _client.close();
  }
}

class SessionRepositoryException implements Exception {
  SessionRepositoryException(this.message);
  final String message;

  @override
  String toString() => message;
}
