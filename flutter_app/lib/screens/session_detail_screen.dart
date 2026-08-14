import 'package:flutter/material.dart';

import '../models/session_summary.dart';
import '../theme.dart';

/// Matches design/mockups/session_detail_mockup.html. Summary tab is a
/// placeholder reserved for Phase 8's debrief content — not built yet, per
/// the build plan.
class SessionDetailScreen extends StatefulWidget {
  const SessionDetailScreen({super.key, required this.session});

  final SessionSummary session;

  @override
  State<SessionDetailScreen> createState() => _SessionDetailScreenState();
}

class _SessionDetailScreenState extends State<SessionDetailScreen> {
  int _tab = 0;

  @override
  Widget build(BuildContext context) {
    final session = widget.session;
    return Scaffold(
      backgroundColor: CopilotColors.ink,
      appBar: CopilotHeader(
        title: session.title,
        subtitle: session.dateLabel,
        onBack: () => Navigator.of(context).pop(),
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 6, 20, 16),
            child: Row(
              children: [
                _Stat(value: session.durationLabel, label: 'Duration'),
                const SizedBox(width: 8),
                _Stat(
                  value: '${session.suggestionCount}',
                  label: 'Suggestions',
                  accent: true,
                ),
                const SizedBox(width: 8),
                _Stat(value: '${session.turnCount}', label: 'Turns'),
              ],
            ),
          ),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 20),
            decoration: const BoxDecoration(
              border: Border(bottom: BorderSide(color: CopilotColors.hairline)),
            ),
            child: Row(
              children: [
                _TabLabel(
                  label: 'Transcript',
                  active: _tab == 0,
                  onTap: () => setState(() => _tab = 0),
                ),
                _TabLabel(
                  label: 'Suggestions',
                  active: _tab == 1,
                  onTap: () => setState(() => _tab = 1),
                ),
                _TabLabel(
                  label: 'Summary',
                  active: _tab == 2,
                  onTap: () => setState(() => _tab = 2),
                ),
              ],
            ),
          ),
          Expanded(
            child: switch (_tab) {
              0 => _TranscriptTab(session: session),
              1 => _SuggestionsTab(session: session),
              _ => const _SummaryTab(),
            },
          ),
          Container(
            padding: const EdgeInsets.fromLTRB(20, 14, 20, 22),
            decoration: const BoxDecoration(
              border: Border(top: BorderSide(color: CopilotColors.hairline)),
            ),
            child: Row(
              children: [
                Expanded(
                  child: _ActionButton(
                    icon: Icons.ios_share,
                    label: 'Export',
                    onTap: () {
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(content: Text('Export not wired up yet.')),
                      );
                    },
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: _ActionButton(
                    icon: Icons.delete_outline,
                    label: 'Delete',
                    onTap: () => _confirmDelete(context),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  void _confirmDelete(BuildContext context) {
    showDialog(
      context: context,
      builder: (dialogContext) => AlertDialog(
        backgroundColor: CopilotColors.panel,
        title: const Text('Delete session?', style: TextStyle(color: CopilotColors.paper)),
        content: const Text(
          'This removes the transcript and suggestions for this session. '
          'There is no persistence layer yet, so this only clears it from '
          'the current view.',
          style: TextStyle(color: CopilotColors.mist),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () {
              Navigator.of(dialogContext).pop();
              Navigator.of(context).pop();
            },
            child: const Text('Delete', style: TextStyle(color: CopilotColors.amber)),
          ),
        ],
      ),
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat({required this.value, required this.label, this.accent = false});

  final String value;
  final String label;
  final bool accent;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(
          color: CopilotColors.panel,
          borderRadius: BorderRadius.circular(10),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              value,
              style: TextStyle(
                fontFamily: CopilotFonts.mono,
                fontSize: 16,
                fontWeight: FontWeight.w500,
                color: accent ? CopilotColors.blue : CopilotColors.paper,
              ),
            ),
            const SizedBox(height: 3),
            Text(
              label.toUpperCase(),
              style: const TextStyle(
                fontFamily: CopilotFonts.mono,
                fontSize: 9.5,
                letterSpacing: 0.5,
                color: CopilotColors.mist,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _TabLabel extends StatelessWidget {
  const _TabLabel({required this.label, required this.active, required this.onTap});

  final String label;
  final bool active;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(right: 18),
      child: GestureDetector(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.only(bottom: 12),
          decoration: BoxDecoration(
            border: Border(
              bottom: BorderSide(
                color: active ? CopilotColors.amber : Colors.transparent,
                width: 2,
              ),
            ),
          ),
          child: Text(
            label,
            style: TextStyle(
              fontFamily: CopilotFonts.display,
              fontWeight: FontWeight.w500,
              fontSize: 12.5,
              color: active ? CopilotColors.paper : CopilotColors.mist,
            ),
          ),
        ),
      ),
    );
  }
}

class _TranscriptTab extends StatelessWidget {
  const _TranscriptTab({required this.session});

  final SessionSummary session;

  @override
  Widget build(BuildContext context) {
    if (session.transcript.isEmpty) {
      return const _EmptyTab(text: 'No transcript recorded for this session.');
    }
    return ListView.separated(
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 20),
      itemCount: session.transcript.length,
      separatorBuilder: (_, __) => const SizedBox(height: 16),
      itemBuilder: (context, i) {
        final line = session.transcript[i];
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              line.speaker.toUpperCase(),
              style: const TextStyle(
                fontFamily: CopilotFonts.mono,
                fontSize: 10,
                letterSpacing: 1,
                color: CopilotColors.mist,
              ),
            ),
            const SizedBox(height: 4),
            Text(
              line.text,
              style: const TextStyle(fontSize: 14, height: 1.55, color: CopilotColors.paper),
            ),
            if (line.suggestion != null) _SuggestionCard(suggestion: line.suggestion!),
          ],
        );
      },
    );
  }
}

class _SuggestionsTab extends StatelessWidget {
  const _SuggestionsTab({required this.session});

  final SessionSummary session;

  @override
  Widget build(BuildContext context) {
    final suggestions =
        session.transcript.map((l) => l.suggestion).whereType<SuggestionRecord>().toList();
    if (suggestions.isEmpty) {
      return const _EmptyTab(text: 'No suggestions were generated for this session.');
    }
    return ListView.separated(
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 20),
      itemCount: suggestions.length,
      separatorBuilder: (_, __) => const SizedBox(height: 14),
      itemBuilder: (context, i) {
        final s = suggestions[i];
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              s.question,
              style: const TextStyle(
                fontSize: 12.5,
                fontWeight: FontWeight.w500,
                color: CopilotColors.mist,
              ),
            ),
            const SizedBox(height: 4),
            _SuggestionCard(suggestion: s),
          ],
        );
      },
    );
  }
}

class _SummaryTab extends StatelessWidget {
  const _SummaryTab();

  @override
  Widget build(BuildContext context) {
    // Placeholder — reserved for Phase 8's debrief content, not built yet.
    return const _EmptyTab(
      text: 'Session summaries arrive with Phase 8 (Debrief) — not built yet.',
    );
  }
}

class _EmptyTab extends StatelessWidget {
  const _EmptyTab({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 40),
        child: Text(
          text,
          textAlign: TextAlign.center,
          style: const TextStyle(color: CopilotColors.mist, fontSize: 13, height: 1.5),
        ),
      ),
    );
  }
}

class _SuggestionCard extends StatelessWidget {
  const _SuggestionCard({required this.suggestion});

  final SuggestionRecord suggestion;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(top: 6),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: const BoxDecoration(
        color: CopilotColors.panel,
        border: Border(left: BorderSide(color: CopilotColors.blue, width: 3)),
        borderRadius: BorderRadius.horizontal(right: Radius.circular(10)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Row(
            children: [
              Icon(Icons.lightbulb_outline, size: 12, color: CopilotColors.blue),
              SizedBox(width: 6),
              Text(
                'Suggested answer',
                style: TextStyle(
                  fontFamily: CopilotFonts.display,
                  fontWeight: FontWeight.w500,
                  fontSize: 11.5,
                  color: CopilotColors.blue,
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            suggestion.answer,
            style: const TextStyle(fontSize: 13, height: 1.5, color: CopilotColors.paper),
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Text(suggestion.level,
                  style: const TextStyle(
                      fontFamily: CopilotFonts.mono, fontSize: 9.5, color: CopilotColors.mist)),
              if (suggestion.latencyMs != null) ...[
                const SizedBox(width: 12),
                Text('${suggestion.latencyMs}ms',
                    style: const TextStyle(
                        fontFamily: CopilotFonts.mono, fontSize: 9.5, color: CopilotColors.mist)),
              ],
              if (suggestion.confidencePct != null) ...[
                const SizedBox(width: 12),
                Text('conf ${suggestion.confidencePct}%',
                    style: const TextStyle(
                        fontFamily: CopilotFonts.mono, fontSize: 9.5, color: CopilotColors.mist)),
              ],
            ],
          ),
        ],
      ),
    );
  }
}

class _ActionButton extends StatelessWidget {
  const _ActionButton({required this.icon, required this.label, required this.onTap});

  final IconData icon;
  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 11),
        decoration: BoxDecoration(
          color: CopilotColors.panel,
          borderRadius: BorderRadius.circular(10),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(icon, size: 14, color: CopilotColors.paper),
            const SizedBox(width: 7),
            Text(
              label,
              style: const TextStyle(
                fontFamily: CopilotFonts.display,
                fontWeight: FontWeight.w500,
                fontSize: 12.5,
                color: CopilotColors.paper,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
