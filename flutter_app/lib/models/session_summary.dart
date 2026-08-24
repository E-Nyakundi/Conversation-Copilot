/// Lightweight session-history record used to drive Home's "Recent
/// Sessions" list, the Session History screen, and Session Detail.
///
/// Real data as of 2026-08-24 — see data/session_repository.dart, which
/// builds these from the server's REST API (api/rest_views.py) instead of
/// the hardcoded mockSessions constant this file used to also define. The
/// shape here is unchanged from the mock-data version on purpose, so the
/// screens that already consumed it didn't need restructuring, only their
/// data source swapped.
class SessionSummary {
  const SessionSummary({
    required this.id,
    required this.title,
    required this.dateLabel,
    required this.monthLabel,
    required this.durationLabel,
    required this.hadSuggestions,
    required this.suggestionCount,
    required this.turnCount,
    this.transcript = const [],
    this.suggestions = const [],
  });

  final String id;
  final String title;
  final String dateLabel;
  final String monthLabel;
  final String durationLabel;
  final bool hadSuggestions;
  final int suggestionCount;
  final int turnCount;
  final List<TranscriptLine> transcript;
  final List<SuggestionRecord> suggestions;
}

class TranscriptLine {
  const TranscriptLine({
    required this.speaker,
    required this.text,
    this.suggestion,
  });
  final String speaker;
  final String text;
  final SuggestionRecord? suggestion;
}

class SuggestionRecord {
  const SuggestionRecord({
    required this.question,
    required this.answer,
    required this.level,
    this.latencyMs,
    this.confidencePct,
  });
  final String question;
  final String answer;
  final String level;
  final int? latencyMs;
  final int? confidencePct;
}
