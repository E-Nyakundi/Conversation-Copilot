import 'package:flutter/material.dart';

import '../theme.dart';
import 'tour_controller.dart';

/// Full-app hint system (2026-08-31, build plan §16): the "stays
/// available on-demand" half of Lewis's spec. Drop this anywhere a
/// screen has its own TourScreen defined (see main.dart's AppShell for
/// where those live) — tapping it calls
/// [TourController.replayScreen] for [screenId], which re-runs that
/// screen's steps through the same TourOverlay/TourStep machinery the
/// mandatory first-run tour uses, without touching
/// [TourController.completed] for the rest of the app (see
/// replayScreen's own doc comment).
///
/// Deliberately reuses the mandatory tour's own step data rather than a
/// separate hint dataset — one set of tips, shown either automatically
/// (first run) or on demand (this button), never two things to keep in
/// sync.
class TourHelpButton extends StatelessWidget {
  const TourHelpButton({super.key, required this.tour, required this.screenId});

  final TourController tour;
  final String screenId;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () => tour.replayScreen(screenId),
      child: Container(
        width: 32,
        height: 32,
        decoration: const BoxDecoration(
          color: CopilotColors.panel,
          shape: BoxShape.circle,
        ),
        child: const Icon(Icons.help_outline, color: CopilotColors.mist, size: 16),
      ),
    );
  }
}
