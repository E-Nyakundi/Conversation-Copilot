import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'tour_step.dart';

/// Full-app hint system (2026-08-31, build plan §16): the state machine
/// behind Lewis's spec — "continuous onboarding right from when the user
/// first opens the app up to the very end, even the settings page... all
/// pages... shouldn't be able to actually do anything there until it
/// completes the onboarding."
///
/// Lifecycle, matching main.dart's existing gating pattern (see
/// `_onboarded`/`OnboardingScreen`) rather than inventing a new one:
/// - [completed] == false (fresh install, or explicitly reset) -> the app
///   is in mandatory-tour mode. AppShell (see main.dart) consults
///   [currentScreenId] to know which of its IndexedStack tabs to force
///   into view, and wraps the whole shell in a gating widget (see
///   tour_gate.dart) that blocks every gesture except the tour's own
///   Next button and, when the current step [TourStep.requiresTask] is
///   true, the one specific action that satisfies that task.
/// - [completed] == true -> tour is over. The SAME TourScreen/TourStep
///   definitions remain available on-demand (a help icon can call
///   [replayScreen] any time) — this is the "stays available on-demand"
///   half of the spec, reusing the mandatory tour's own step data rather
///   than maintaining two separate hint datasets.
///
/// Persistence: a single bool in shared_preferences, same storage tier
/// as main.dart's own `_onboardedKey` (a UI/progress flag, not a
/// credential — see auth_store.dart's doc comment on why the auth token
/// deliberately lives in secure storage instead and this doesn't).
class TourController extends ChangeNotifier {
  TourController({required this.screenOrder, required this.screens});

  /// Ordered list of screenIds the mandatory tour walks through — e.g.
  /// `['home', 'history', 'settings', 'live_session']`. Must match
  /// AppShell's actual navigation graph (its three IndexedStack tabs plus
  /// LiveSessionScreen's pushed route) or the tour will try to force a
  /// screen the shell doesn't know how to show — see main.dart's AppShell
  /// integration for how [currentScreenId] is consumed.
  final List<String> screenOrder;

  /// screenId -> that screen's TourScreen definition. A screenId present
  /// in [screenOrder] but missing here is treated as a no-op step (the
  /// tour advances straight through it) rather than crashing — a screen
  /// with nothing to showcase yet (see the build-plan note on which
  /// screens still need their TourScreen written) shouldn't block the
  /// whole walkthrough.
  final Map<String, TourScreen> screens;

  static const _completedKey = 'copilot_tour_completed';
  static const _seenStepsKey = 'copilot_tour_seen_steps';

  bool _completed = false;
  bool _loaded = false;
  int _screenIndex = 0;
  int _stepIndex = 0;
  Set<String> _seenStepIds = {};

  bool get loaded => _loaded;
  bool get completed => _completed;

  /// True while the mandatory walkthrough is actively running — the
  /// signal tour_gate.dart's blocking overlay checks. Distinct from
  /// `!completed` alone because [replayScreen] also drives the tour
  /// machinery (same step-by-step mechanism) after the mandatory pass is
  /// done, without that being "back in mandatory mode" — see
  /// [_replaying].
  bool get isActive => (!_completed || _replaying) && _loaded;
  bool _replaying = false;

  String? get currentScreenId =>
      _screenIndex < screenOrder.length ? screenOrder[_screenIndex] : null;

  TourStep? get currentStep {
    final screenId = currentScreenId;
    if (screenId == null) return null;
    final steps = screens[screenId]?.steps ?? const [];
    return _stepIndex < steps.length ? steps[_stepIndex] : null;
  }

  Future<void> load() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      _completed = prefs.getBool(_completedKey) ?? false;
      _seenStepIds = (prefs.getStringList(_seenStepsKey) ?? const []).toSet();
    } catch (_) {
      // Best-effort, same posture as main.dart's _markOnboarded: if this
      // read fails, the tour just runs again next cold start rather than
      // crashing startup.
      _completed = false;
    }
    _loaded = true;
    notifyListeners();
  }

  /// Advances past the current step. If the current step
  /// [TourStep.requiresTask] is true, this is a no-op — see
  /// [completeCurrentTask], which is what actually unblocks a task-gated
  /// step. Calling [advance] on a non-task step is what the tour's
  /// "Next" button does.
  void advance() {
    final step = currentStep;
    if (step != null && step.requiresTask) return;
    _advanceInternal();
  }

  /// Screen-specific code (e.g. LiveSessionScreen's mic-tap handler)
  /// calls this once the task a [TourStep.requiresTask] step describes
  /// has actually been completed — Lewis's "component tips + a short
  /// task ... before advancing". [stepId] must match the currently
  /// active step's id, or this is a no-op (guards against a stale
  /// callback firing after the tour has already moved on, e.g. from a
  /// widget that outlives the step it was gating).
  void completeCurrentTask(String stepId) {
    final step = currentStep;
    if (step == null || step.id != stepId) return;
    _advanceInternal();
  }

  void _advanceInternal() {
    final step = currentStep;
    if (step != null) _seenStepIds.add(step.id);

    final steps = screens[currentScreenId]?.steps ?? const [];
    if (_stepIndex + 1 < steps.length) {
      _stepIndex++;
    } else {
      _screenIndex++;
      _stepIndex = 0;
      // Skip any screen with no steps defined yet (see [screens]'s doc
      // comment) rather than showing an empty tour stop.
      while (_screenIndex < screenOrder.length &&
          (screens[screenOrder[_screenIndex]]?.steps.isEmpty ?? true)) {
        _screenIndex++;
      }
    }

    if (_screenIndex >= screenOrder.length) {
      if (_replaying) {
        _replaying = false;
      } else {
        _completed = true;
        _persistCompleted();
      }
      _screenIndex = 0;
      _stepIndex = 0;
    }
    _persistSeenSteps();
    notifyListeners();
  }

  /// On-demand replay (Lewis's "stays available on-demand" half of the
  /// spec, applied here as replaying a single screen's tips rather than
  /// the whole app — see tour_step.dart's TourScreen doc comment). Does
  /// NOT touch [completed] — a user who's already finished the mandatory
  /// tour and taps a help icon on Settings later isn't put back into
  /// first-run mode for the rest of the app, only that one screen's
  /// steps run again.
  void replayScreen(String screenId) {
    final index = screenOrder.indexOf(screenId);
    if (index == -1) return;
    _replaying = true;
    _screenIndex = index;
    _stepIndex = 0;
    notifyListeners();
  }

  bool hasSeen(String stepId) => _seenStepIds.contains(stepId);

  Future<void> _persistCompleted() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setBool(_completedKey, true);
    } catch (_) {
      // Best-effort — see load()'s comment; worst case the tour reruns.
    }
  }

  Future<void> _persistSeenSteps() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setStringList(_seenStepsKey, _seenStepIds.toList());
    } catch (_) {
      // Best-effort, same as above — [hasSeen] just falls back to
      // "unseen" for everything next cold start, which only affects the
      // on-demand replay/badge use case, never the mandatory tour itself
      // (that's gated on [_completed], persisted separately).
    }
  }
}
