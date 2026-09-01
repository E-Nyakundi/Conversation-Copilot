import 'package:flutter/material.dart';

import '../data/session_repository.dart';
import '../models/session_summary.dart';
import '../theme.dart';
import 'session_detail_screen.dart';

enum _HistoryFilter { all, hasSuggestions, thisWeek }

/// Matches design/mockups/session_history_mockup.html. Works both as a
/// pushed route (from Home's "View all", or Session Detail's back target)
/// and as a bottom-nav tab root (`showBackButton: false`) — see
/// AppShell in main.dart.
///
/// Real data as of 2026-08-24 — fetches from SessionRepository instead of
/// the old mockSessions constant. Filtering/search still run client-side
/// over the fetched list (small enough dataset for now that a server-side
/// search endpoint isn't worth it yet).
class SessionHistoryScreen extends StatefulWidget {
  const SessionHistoryScreen({
    super.key,
    this.showBackButton = true,
    this.searchKey,
    this.filtersKey,
    this.headerTrailing,
  });

  final bool showBackButton;

  // Full-app hint system (2026-08-31, build plan §16): stable GlobalKeys
  // for the tour — same pattern as home_screen.dart/settings_screen.dart,
  // owned by AppShell's State, not created here.
  final GlobalKey? searchKey;
  final GlobalKey? filtersKey;

  /// Passed straight through to CopilotHeader's own `trailing` slot —
  /// AppShell uses this to place a TourHelpButton without this screen
  /// needing to know anything about the tour system itself.
  final Widget? headerTrailing;

  @override
  State<SessionHistoryScreen> createState() => _SessionHistoryScreenState();
}

class _SessionHistoryScreenState extends State<SessionHistoryScreen> {
  final _searchController = TextEditingController();
  final _repository = SessionRepository();
  _HistoryFilter _filter = _HistoryFilter.all;
  String _query = '';
  late Future<List<SessionSummary>> _sessionsFuture;

  @override
  void initState() {
    super.initState();
    _sessionsFuture = _repository.fetchSessions();
  }

  @override
  void dispose() {
    _searchController.dispose();
    _repository.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    setState(() {
      _sessionsFuture = _repository.fetchSessions();
    });
    await _sessionsFuture;
  }

  List<SessionSummary> _filtered(List<SessionSummary> all) {
    var list = all;
    if (_filter == _HistoryFilter.hasSuggestions) {
      list = list.where((s) => s.hadSuggestions).toList();
    } else if (_filter == _HistoryFilter.thisWeek) {
      // SessionSummary only carries display strings (dateLabel/monthLabel),
      // not a raw DateTime — same shape the mock version used, so a real
      // "last 7 days" filter isn't possible without a model change.
      // Approximated the same way the original mock chip was: since the
      // API returns sessions newest-first, the first few rows in the
      // (already ordered) list stand in for "this week". Revisit if
      // SessionSummary ever grows a real startedAt field.
      const thisWeekApproxCount = 5;
      list = list.take(thisWeekApproxCount).toList();
    }
    if (_query.trim().isNotEmpty) {
      final q = _query.trim().toLowerCase();
      list = list.where((s) => s.title.toLowerCase().contains(q)).toList();
    }
    return list;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: CopilotColors.ink,
      appBar: CopilotHeader(
        title: 'Session History',
        onBack: widget.showBackButton ? () => Navigator.of(context).pop() : null,
        trailing: widget.headerTrailing,
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
            child: Container(
              key: widget.searchKey,
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
              key: widget.filtersKey,
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
            child: RefreshIndicator(
              onRefresh: _refresh,
              color: CopilotColors.amber,
              backgroundColor: CopilotColors.panel,
              child: FutureBuilder<List<SessionSummary>>(
                future: _sessionsFuture,
                builder: (context, snapshot) {
                  if (snapshot.connectionState == ConnectionState.waiting) {
                    return ListView(
                      physics: const AlwaysScrollableScrollPhysics(),
                      children: const [
                        SizedBox(height: 60),
                        Center(
                          child: SizedBox(
                            width: 20,
                            height: 20,
                            child: CircularProgressIndicator(
                              strokeWidth: 2,
                              color: CopilotColors.amber,
                            ),
                          ),
                        ),
                      ],
                    );
                  }
                  if (snapshot.hasError) {
                    return ListView(
                      physics: const AlwaysScrollableScrollPhysics(),
                      children: [
                        const SizedBox(height: 60),
                        const Icon(Icons.cloud_off, color: CopilotColors.mist, size: 22),
                        const SizedBox(height: 10),
                        const Center(
                          child: Text(
                            'Couldn\'t load session history.',
                            style: TextStyle(color: CopilotColors.mist, fontSize: 12.5),
                          ),
                        ),
                        const SizedBox(height: 10),
                        Center(
                          child: GestureDetector(
                            onTap: _refresh,
                            child: const Text(
                              'Retry',
                              style: TextStyle(
                                fontFamily: CopilotFonts.mono,
                                fontSize: 11,
                                color: CopilotColors.blue,
                              ),
                            ),
                          ),
                        ),
                      ],
                    );
                  }

                  final sessions = _filtered(snapshot.data ?? const []);
                  final months = <String>[];
                  for (final s in sessions) {
                    if (!months.contains(s.monthLabel)) months.add(s.monthLabel);
                  }

                  if (sessions.isEmpty) {
                    return ListView(
                      physics: const AlwaysScrollableScrollPhysics(),
                      children: const [
                        SizedBox(height: 60),
                        _EmptyHistory(),
                      ],
                    );
                  }

                  return ListView(
                    physics: const AlwaysScrollableScrollPhysics(),
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
                  );
                },
              ),
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
          MaterialPageRoute(builder: (_) => SessionDetailScreen.forSession(session)),
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
