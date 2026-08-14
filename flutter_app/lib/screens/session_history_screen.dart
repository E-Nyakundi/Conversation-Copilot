import 'package:flutter/material.dart';

import '../models/session_summary.dart';
import '../theme.dart';
import 'session_detail_screen.dart';

enum _HistoryFilter { all, hasSuggestions, thisWeek }

/// Matches design/mockups/session_history_mockup.html. Works both as a
/// pushed route (from Home's "View all", or Session Detail's back target)
/// and as a bottom-nav tab root (`showBackButton: false`) — see
/// AppShell in main.dart.
class SessionHistoryScreen extends StatefulWidget {
  const SessionHistoryScreen({super.key, this.showBackButton = true});

  final bool showBackButton;

  @override
  State<SessionHistoryScreen> createState() => _SessionHistoryScreenState();
}

class _SessionHistoryScreenState extends State<SessionHistoryScreen> {
  final _searchController = TextEditingController();
  _HistoryFilter _filter = _HistoryFilter.all;
  String _query = '';

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  List<SessionSummary> get _filtered {
    var list = mockSessions;
    if (_filter == _HistoryFilter.hasSuggestions) {
      list = list.where((s) => s.hadSuggestions).toList();
    } else if (_filter == _HistoryFilter.thisWeek) {
      // No real dates on the mock data — approximate "this week" as the
      // first two (most recent) sessions, same intent as the mockup's chip.
      list = list.take(2).toList();
    }
    if (_query.trim().isNotEmpty) {
      final q = _query.trim().toLowerCase();
      list = list.where((s) => s.title.toLowerCase().contains(q)).toList();
    }
    return list;
  }

  @override
  Widget build(BuildContext context) {
    final sessions = _filtered;
    final months = <String>[];
    for (final s in sessions) {
      if (!months.contains(s.monthLabel)) months.add(s.monthLabel);
    }

    return Scaffold(
      backgroundColor: CopilotColors.ink,
      appBar: CopilotHeader(
        title: 'Session History',
        onBack: widget.showBackButton ? () => Navigator.of(context).pop() : null,
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
            child: Container(
              decoration: BoxDecoration(
                color: CopilotColors.panel,
                borderRadius: BorderRadius.circular(10),
              ),
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
              child: Row(
                children: [
                  const Icon(Icons.search, size: 15, color: CopilotColors.mist),
                  const SizedBox(width: 9),
                  Expanded(
                    child: TextField(
                      controller: _searchController,
                      onChanged: (v) => setState(() => _query = v),
                      style: const TextStyle(
                          fontSize: 13, color: CopilotColors.paper),
                      decoration: const InputDecoration(
                        border: InputBorder.none,
                        isDense: true,
                        contentPadding: EdgeInsets.symmetric(vertical: 10),
                        hintText: 'Search transcripts...',
                        hintStyle: TextStyle(color: CopilotColors.mist),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 0, 20, 16),
            child: Row(
              children: [
                _FilterChip(
                  label: 'All',
                  active: _filter == _HistoryFilter.all,
                  onTap: () => setState(() => _filter = _HistoryFilter.all),
                ),
                const SizedBox(width: 8),
                _FilterChip(
                  label: 'Has suggestions',
                  active: _filter == _HistoryFilter.hasSuggestions,
                  onTap: () =>
                      setState(() => _filter = _HistoryFilter.hasSuggestions),
                ),
                const SizedBox(width: 8),
                _FilterChip(
                  label: 'This week',
                  active: _filter == _HistoryFilter.thisWeek,
                  onTap: () => setState(() => _filter = _HistoryFilter.thisWeek),
                ),
              ],
            ),
          ),
          Expanded(
            child: sessions.isEmpty
                ? const _EmptyHistory()
                : ListView(
                    padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
                    children: [
                      for (final month in months) ...[
                        Padding(
                          padding: EdgeInsets.only(top: month == months.first ? 0 : 14, bottom: 10),
                          child: Text(
                            month.toUpperCase(),
                            style: const TextStyle(
                              fontFamily: CopilotFonts.mono,
                              fontSize: 10.5,
                              letterSpacing: 1.5,
                              color: CopilotColors.mist,
                            ),
                          ),
                        ),
                        for (final s in sessions.where((s) => s.monthLabel == month))
                          _HistoryCard(session: s),
                      ],
                    ],
                  ),
          ),
        ],
      ),
    );
  }
}

class _FilterChip extends StatelessWidget {
  const _FilterChip({required this.label, required this.active, required this.onTap});

  final String label;
  final bool active;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        decoration: BoxDecoration(
          color: active ? CopilotColors.panelRaised : CopilotColors.panel,
          borderRadius: BorderRadius.circular(20),
          border: active ? Border.all(color: CopilotColors.amberDim) : null,
        ),
        child: Text(
          label,
          style: TextStyle(
            fontFamily: CopilotFonts.mono,
            fontSize: 10.5,
            letterSpacing: 0.3,
            color: active ? CopilotColors.amber : CopilotColors.mist,
          ),
        ),
      ),
    );
  }
}

class _HistoryCard extends StatelessWidget {
  const _HistoryCard({required this.session});

  final SessionSummary session;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      borderRadius: BorderRadius.circular(12),
      onTap: () {
        Navigator.of(context).push(
          MaterialPageRoute(builder: (_) => SessionDetailScreen(session: session)),
        );
      },
      child: Container(
        margin: const EdgeInsets.only(bottom: 9),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 13),
        decoration: BoxDecoration(
          color: CopilotColors.panel,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Row(
          children: [
            Container(
              width: 7,
              height: 7,
              margin: const EdgeInsets.only(right: 11),
              decoration: BoxDecoration(
                color: session.hadSuggestions ? CopilotColors.blue : CopilotColors.hairline,
                shape: BoxShape.circle,
              ),
            ),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    session.title,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                        fontSize: 13.5, fontWeight: FontWeight.w500, color: CopilotColors.paper),
                  ),
                  const SizedBox(height: 3),
                  Text(
                    session.hadSuggestions
                        ? '${session.dateLabel} · ${session.suggestionCount} suggestions'
                        : session.dateLabel,
                    style: const TextStyle(
                        fontFamily: CopilotFonts.mono, fontSize: 10.5, color: CopilotColors.mist),
                  ),
                ],
              ),
            ),
            const Icon(Icons.chevron_right, size: 14, color: CopilotColors.mist),
          ],
        ),
      ),
    );
  }
}

class _EmptyHistory extends StatelessWidget {
  const _EmptyHistory();

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 40),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.search_off, color: CopilotColors.mist, size: 28),
            const SizedBox(height: 10),
            Text(
              'No sessions match',
              style: const TextStyle(color: CopilotColors.mist, fontSize: 13),
            ),
          ],
        ),
      ),
    );
  }
}
