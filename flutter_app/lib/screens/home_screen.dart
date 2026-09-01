import 'package:flutter/material.dart';

import '../data/session_repository.dart';
import '../models/session_summary.dart';
import '../theme.dart';
import 'session_detail_screen.dart';

/// Entry screen: matches design/mockups/home_dashboard_mockup.html. One
/// primary action (Start Session, reusing the amplitude-ring motif) + a
/// Recent Sessions list. The mockup puts Settings behind a header gear
/// icon; the implemented app shell (see main.dart) instead uses a bottom
/// nav with Home/History/Settings as siblings, so the gear here just jumps
/// to that Settings tab rather than pushing a new route — same one-tap
/// access the mockup intended, fitting the nav model actually shipped.
///
/// Real data as of 2026-08-24: fetches recent sessions from
/// SessionRepository instead of the old mockSessions constant — see that
/// file's doc comment for why this became a StatefulWidget (a fetch needs
/// somewhere to hold loading/error/data state; the mock version was a
/// StatelessWidget since a const list needs none of that).
class HomeScreen extends StatefulWidget {
  const HomeScreen({
    super.key,
    required this.onStartSession,
    this.onOpenSettings,
    this.onViewAllSessions,
    this.settingsKey,
    this.startSessionKey,
    this.viewAllKey,
    this.headerTrailing,
  });

  final VoidCallback onStartSession;
  final VoidCallback? onOpenSettings;
  final VoidCallback? onViewAllSessions;

  /// An extra widget placed in the top-right header row, alongside the
  /// settings gear — AppShell uses this to place a TourHelpButton
  /// without this screen needing to know anything about the tour system
  /// itself, same pattern as headerTrailing on SessionHistoryScreen and
  /// SettingsScreen (build plan §16) even though this screen doesn't use
  /// CopilotHeader at all — its header row is hand-built (see build()
  /// below), so this is a plain extra child rather than a passthrough to
  /// a shared widget's own trailing slot.
  final Widget? headerTrailing;

  // Full-app hint system (2026-08-31, build plan §16): stable GlobalKeys
  // for the tour to target, owned by the caller (AppShell) rather than
  // created here — a key created inside this widget's own build() would
  // be a NEW GlobalKey every rebuild (e.g. every tab switch via the
  // IndexedStack in main.dart), which breaks TourOverlay's ability to
  // resolve a stable RenderBox across frames. See main.dart's AppShell
  // for where these are actually instantiated (once, in State, not in
  // build()) and lib/tour/tour_step.dart for how they're wired into a
  // TourScreen definition.
  final GlobalKey? settingsKey;
  final GlobalKey? startSessionKey;
  final GlobalKey? viewAllKey;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final _repository = SessionRepository();
  late Future<List<SessionSummary>> _sessionsFuture;

  @override
  void initState() {
    super.initState();
    _sessionsFuture = _repository.fetchSessions();
  }

  @override
  void dispose() {
    _repository.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    setState(() {
      _sessionsFuture = _repository.fetchSessions();
    });
    await _sessionsFuture;
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: RefreshIndicator(
        onRefresh: _refresh,
        color: CopilotColors.amber,
        backgroundColor: CopilotColors.panel,
        child: SingleChildScrollView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  const Text.rich(
                    TextSpan(
                      style: TextStyle(
                        fontFamily: CopilotFonts.mono,
                        fontSize: 13,
                        letterSpacing: 2,
                        color: CopilotColors.mist,
                      ),
                      children: [
                        TextSpan(text: 'CO'),
                        TextSpan(
                          text: '/',
                          style: TextStyle(color: CopilotColors.paper),
                        ),
                        TextSpan(text: 'PILOT'),
                      ],
                    ),
                  ),
                  Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      if (widget.headerTrailing != null) ...[
                        widget.headerTrailing!,
                        const SizedBox(width: 10),
                      ],
                      if (widget.onOpenSettings != null)
                        GestureDetector(
                          key: widget.settingsKey,
                          onTap: widget.onOpenSettings,
                          child: Container(
                            width: 34,
                            height: 34,
                            decoration: const BoxDecoration(
                              color: CopilotColors.panel,
                              shape: BoxShape.circle,
                            ),
                            child: const Icon(Icons.settings_outlined,
                                color: CopilotColors.mist, size: 17),
                          ),
                        ),
                    ],
                  ),
                ],
              ),
              const SizedBox(height: 18),
              Center(
                child: Column(
                  children: [
                    GestureDetector(
                      key: widget.startSessionKey,
                      onTap: widget.onStartSession,
                      child: Container(
                        width: 132,
                        height: 132,
                        decoration: BoxDecoration(
                          color: CopilotColors.panel,
                          shape: BoxShape.circle,
                          border: Border.all(color: CopilotColors.amber, width: 2),
                        ),
                        child: const Icon(Icons.mic_none, color: CopilotColors.amber, size: 44),
                      ),
                    ),
                    const SizedBox(height: 14),
                    const Text(
                      'Start Session',
                      style: TextStyle(
                        fontFamily: CopilotFonts.display,
                        fontWeight: FontWeight.w600,
                        fontSize: 16,
                        color: CopilotColors.paper,
                      ),
                    ),
                  ],
                ),
              ),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  const SectionLabel('Recent Sessions', topPad: 28),
                  Padding(
                    padding: const EdgeInsets.only(top: 20, bottom: 8),
                    child: GestureDetector(
                      key: widget.viewAllKey,
                      onTap: widget.onViewAllSessions,
                      child: const Text(
                        'View all',
                        style: TextStyle(
                          fontFamily: CopilotFonts.mono,
                          fontSize: 11,
                          color: CopilotColors.blue,
                        ),
                      ),
                    ),
                  ),
                ],
              ),
              FutureBuilder<List<SessionSummary>>(
                future: _sessionsFuture,
                builder: (context, snapshot) {
                  if (snapshot.connectionState == ConnectionState.waiting) {
                    return const _RecentSessionsLoading();
                  }
                  if (snapshot.hasError) {
                    return _RecentSessionsError(onRetry: _refresh);
                  }
                  final sessions = snapshot.data ?? const [];
                  if (sessions.isEmpty) {
                    return const _RecentSessionsEmpty();
                  }
                  return CopilotGroup(
                    children: [
                      for (final session in sessions.take(3))
                        _SessionRow(session: session),
                    ],
                  );
                },
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _RecentSessionsLoading extends StatelessWidget {
  const _RecentSessionsLoading();

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.symmetric(vertical: 28),
      child: Center(
        child: SizedBox(
          width: 20,
          height: 20,
          child: CircularProgressIndicator(
            strokeWidth: 2,
            color: CopilotColors.amber,
          ),
        ),
      ),
    );
  }
}

class _RecentSessionsError extends StatelessWidget {
  const _RecentSessionsError({required this.onRetry});

  final Future<void> Function() onRetry;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 24),
      child: Column(
        children: [
          const Icon(Icons.cloud_off, color: CopilotColors.mist, size: 22),
          const SizedBox(height: 10),
          const Text(
            'Couldn\'t load recent sessions.',
            style: TextStyle(color: CopilotColors.mist, fontSize: 12.5),
          ),
          const SizedBox(height: 10),
          GestureDetector(
            onTap: onRetry,
            child: const Text(
              'Retry',
              style: TextStyle(
                fontFamily: CopilotFonts.mono,
                fontSize: 11,
                color: CopilotColors.blue,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _RecentSessionsEmpty extends StatelessWidget {
  const _RecentSessionsEmpty();

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.symmetric(vertical: 24),
      child: Center(
        child: Text(
          'No sessions yet — tap Start Session above to record your first one.',
          textAlign: TextAlign.center,
          style: TextStyle(color: CopilotColors.mist, fontSize: 12.5, height: 1.5),
        ),
      ),
    );
  }
}

class _SessionRow extends StatelessWidget {
  const _SessionRow({required this.session});

  final SessionSummary session;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: () {
        Navigator.of(context).push(
          MaterialPageRoute(
            builder: (_) => SessionDetailScreen.forSession(session),
          ),
        );
      },
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        child: Row(
          children: [
            Container(
              width: 8,
              height: 8,
              margin: const EdgeInsets.only(right: 12),
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
                    style: const TextStyle(fontSize: 13.5, color: CopilotColors.paper),
                    overflow: TextOverflow.ellipsis,
                  ),
                  const SizedBox(height: 2),
                  Text(
                    session.hadSuggestions
                        ? '${session.dateLabel} · ${session.suggestionCount} suggestions'
                        : session.dateLabel,
                    style: const TextStyle(
                      fontFamily: CopilotFonts.mono,
                      fontSize: 10,
                      color: CopilotColors.mist,
                    ),
                  ),
                ],
              ),
            ),
            const Icon(Icons.chevron_right, color: CopilotColors.mist, size: 18),
          ],
        ),
      ),
    );
  }
}
