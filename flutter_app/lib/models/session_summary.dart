/// Lightweight session-history record used to drive Home's "Recent
/// Sessions" list, the Session History screen, and Session Detail. This is
/// in-memory/mock data for now — there is no persistence layer or backend
/// endpoint yet for past-session listing (AudioStreamConsumer only handles
/// the live WS protocol). Wiring this to real stored sessions is follow-up
/// work once that API exists; the screens themselves are the deliverable
/// here.
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

/// Placeholder recent-session data so Home/History/Detail have something
/// real to render. Replace with an actual store once past sessions are
/// persisted server-side and exposed via an API.
const mockSessions = <SessionSummary>[
  SessionSummary(
    id: 's4',
    title: 'Backend architecture interview',
    dateLabel: 'Aug 13 · 12:04',
    monthLabel: 'August 2026',
    durationLabel: '32m',
    hadSuggestions: true,
    suggestionCount: 6,
    turnCount: 190,
    transcript: [
      TranscriptLine(
        speaker: 'Interviewer',
        text: 'If queries for products slow down as your data grows, '
            'what would you do?',
        suggestion: SuggestionRecord(
          question: 'If queries for products slow down as your data grows, '
              'what would you do?',
          answer: 'Add indexes on frequently filtered/sorted columns, '
              'introduce caching for repeat reads, and consider '
              'denormalizing hot query paths as scale demands it.',
          level: 'medium',
          latencyMs: 310,
          confidencePct: 78,
        ),
      ),
      TranscriptLine(
        speaker: 'You',
        text: 'I\'d start with indexing frequently queried columns, then '
            'look at caching and denormalization as it grows.',
      ),
      TranscriptLine(
        speaker: 'Interviewer',
        text: 'Why do you personally prefer Django for backend work?',
        suggestion: SuggestionRecord(
          question: 'Why do you personally prefer Django for backend work?',
          answer: 'Its batteries-included approach and ORM speed up '
              'delivery, while the admin panel and auth system remove a '
              'lot of boilerplate for CRUD-heavy backends.',
          level: 'medium',
          latencyMs: 358,
          confidencePct: 90,
        ),
      ),
    ],
  ),
  SessionSummary(
    id: 's3',
    title: 'Client discovery call',
    dateLabel: 'Aug 12 · 31:47',
    monthLabel: 'August 2026',
    durationLabel: '31m',
    hadSuggestions: true,
    suggestionCount: 4,
    turnCount: 140,
    transcript: [
      TranscriptLine(
        speaker: 'Client',
        text: 'What kind of onboarding support do you offer?',
        suggestion: SuggestionRecord(
          question: 'What kind of onboarding support do you offer?',
          answer: 'A dedicated onboarding call in week one, plus async '
              'support during the first 30 days while your team ramps up.',
          level: 'quick',
          latencyMs: 240,
          confidencePct: 82,
        ),
      ),
    ],
  ),
  SessionSummary(
    id: 's2',
    title: 'Quick sync — no questions detected',
    dateLabel: 'Aug 10 · 04:12',
    monthLabel: 'August 2026',
    durationLabel: '4m',
    hadSuggestions: false,
    suggestionCount: 0,
    turnCount: 24,
    transcript: [
      TranscriptLine(speaker: 'A', text: 'Okay, sounds good.'),
      TranscriptLine(speaker: 'B', text: 'Yeah, let\'s pick this up tomorrow.'),
    ],
  ),
  SessionSummary(
    id: 's1',
    title: 'Standup — sprint planning',
    dateLabel: 'Aug 9 · 18:22',
    monthLabel: 'August 2026',
    durationLabel: '18m',
    hadSuggestions: true,
    suggestionCount: 2,
    turnCount: 60,
    transcript: [
      TranscriptLine(speaker: 'A', text: 'Who\'s picking up the auth ticket?'),
    ],
  ),
  SessionSummary(
    id: 's0',
    title: 'Vendor negotiation call',
    dateLabel: 'Jul 29 · 22:03',
    monthLabel: 'July 2026',
    durationLabel: '22m',
    hadSuggestions: true,
    suggestionCount: 5,
    turnCount: 95,
  ),
];
