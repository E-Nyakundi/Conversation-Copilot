import 'package:flutter/material.dart';

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
class HomeScreen extends StatelessWidget {
  const HomeScreen({
    super.key,
    required this.onStartSession,
    this.onOpenSettings,
    this.onViewAllSessions,
  });

  final VoidCallback onStartSession;
  final VoidCallback? onOpenSettings;
  final VoidCallback? onViewAllSessions;

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: SingleChildScrollView(
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
                if (onOpenSettings != null)
                  GestureDetector(
                    onTap: onOpenSettings,
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
            const SizedBox(height: 18),
            Center(
              child: Column(
                children: [
                  GestureDetector(
                    onTap: onStartSession,
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
                    onTap: onViewAllSessions,
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
            CopilotGroup(
              children: [
                for (final session in mockSessions.take(3))
                  _SessionRow(session: session),
              ],
            ),
          ],
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
            builder: (_) => SessionDetailScreen(session: session),
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
