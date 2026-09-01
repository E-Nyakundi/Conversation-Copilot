import 'package:flutter/widgets.dart';

/// Full-app hint system (2026-08-31, build plan §16): one stop on the
/// guided tour — a single showcased widget (via [key], passed straight
/// through to showcaseview's `Showcase(key: ...)`) plus the title/
/// description shown in its tooltip.
///
/// [requiresTask] (Lewis's spec: "component tips + a short task before
/// advancing") — when true, TourController does NOT let the tour advance
/// past this step on a plain "Next" tap; something screen-specific has to
/// call [TourController.completeCurrentTask] first (e.g. Home's Start
/// Session step only advances once the user actually starts a session).
/// When false (the default — most steps, including every step on a
/// first pass through a new screen unless the spec explicitly calls for
/// a task), a "Next" tap advances immediately, same as a normal
/// showcaseview step.
@immutable
class TourStep {
  const TourStep({
    required this.id,
    required this.key,
    required this.title,
    required this.description,
    this.requiresTask = false,
  });

  /// Stable, globally-unique id (e.g. 'home.start_session') — NOT the
  /// GlobalKey itself, which is recreated per widget build. Used for
  /// persistence (which steps has this user already seen, for the
  /// on-demand "replay this screen's tips" case) and for
  /// [TourController.completeCurrentTask] to know which step it's
  /// unblocking without the caller needing a reference to the step
  /// object itself.
  final String id;
  final GlobalKey key;
  final String title;
  final String description;
  final bool requiresTask;
}

/// One screen's worth of tour steps, in the order they should be shown.
/// [screenId] matches the id TourController's screen sequence uses (see
/// tour_controller.dart's `TourController.screenOrder`) — this is how the
/// controller knows which TourScreen definition to pull steps from when
/// it arrives at a given screen during the mandatory walkthrough.
@immutable
class TourScreen {
  const TourScreen({required this.screenId, required this.steps});

  final String screenId;
  final List<TourStep> steps;
}
