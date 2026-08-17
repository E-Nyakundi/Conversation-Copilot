// Phase 4 (2026-08-16) — smart, paced, no-touch code reveal.
//
// Design record: build plan §11b. Built from a real, explicitly stated use
// case, not a guess: the person reading this card is hand-typing the code
// into a *separate* device (not clipboard copy) and cannot touch the phone
// while doing it — they need the phone to hold still on one line long
// enough to type it, then commit to a clean jump to the next line, entirely
// on its own.
//
// Deliberately NOT a smooth/continuous scroll — decided against explicitly
// (see build plan): a moving target fights against "hold still while I
// type this line". Instead this is a stepped reveal: one physical line per
// step, no grouping of trivial short lines (also explicitly decided — a
// lone "}" still gets its own step), each step's hold duration estimated
// from that line's own complexity.
//
// This file is a standalone, non-Flutter-toolchain-verified addition (same
// caveat as every other Flutter change in this build plan) — checked by
// hand for brace/paren balance and Dart syntax, not run through
// `flutter analyze` or a real build.

import 'dart:async';
import 'package:flutter/material.dart';

/// Per-line pacing estimate — how long a line should stay on screen for
/// someone hand-typing it into a separate device (slower than reading
/// speed, closer to hand-typing-code speed).
class LinePacing {
  // Baseline hand-typing-code pace assumption. Deliberately conservative
  // (slower than average reading/typing speed) since the person is
  // transcribing character-by-character to a second device while glancing
  // back and forth, not just reading. Tune here if real sessions show this
  // running too fast/slow — no other call site needs to change.
  static const double msPerChar = 85.0;

  // Minimum hold time for ANY non-trivial line, regardless of how short —
  // "find my place, glance up, start typing" has a real fixed cost that
  // doesn't go to zero even for a short line.
  static const Duration lineFloor = Duration(milliseconds: 900);

  // Trivial lines (blank, or pure closing punctuation like "}", "});",
  // ");") still get their own step per the explicit no-grouping decision,
  // but a much shorter one — there's really nothing to type-check here.
  static const Duration blankLineHold = Duration(milliseconds: 400);
  static const Duration trivialLineHold = Duration(milliseconds: 600);

  // Sane upper cap so one absurdly long/dense line can't stall the whole
  // reveal for an unreasonable amount of time.
  static const Duration lineCap = Duration(milliseconds: 12000);

  static final RegExp _trivialLine = RegExp(r'^[\}\)\];,]+$');

  /// Estimated hold duration for [line] (without its own trailing newline).
  static Duration forLine(String line) {
    final trimmed = line.trim();
    if (trimmed.isEmpty) return blankLineHold;
    if (_trivialLine.hasMatch(trimmed)) return trivialLineHold;

    final meaningfulChars = trimmed.length;
    // Symbol/punctuation density as a rough proxy for typing difficulty —
    // a line dense with generics/lambdas/braces takes longer to get right
    // than the same character count in plain identifiers.
    final symbolCount =
        trimmed.replaceAll(RegExp(r'[A-Za-z0-9\s]'), '').length;
    final symbolDensity = meaningfulChars == 0 ? 0.0 : symbolCount / meaningfulChars;
    final complexityMultiplier = 1.0 + symbolDensity;

    final rawMs = meaningfulChars * msPerChar * complexityMultiplier;
    final ms = rawMs < lineFloor.inMilliseconds
        ? lineFloor.inMilliseconds.toDouble()
        : rawMs;
    final capped = ms > lineCap.inMilliseconds ? lineCap.inMilliseconds.toDouble() : ms;
    return Duration(milliseconds: capped.round());
  }
}

/// Estimated read time for prose (used by the interrupting text overlay,
/// not the code path above) — same "how long does a human need" spirit,
/// tuned for reading rather than hand-typing.
class ReadPacing {
  static const double msPerWord = 260.0; // ~230 wpm, a bit under average adult silent-reading speed, biased slow on purpose
  static const Duration floor = Duration(milliseconds: 1200);
  static const Duration cap = Duration(milliseconds: 15000);

  static Duration forText(String text) {
    final words = text.trim().split(RegExp(r'\s+')).where((w) => w.isNotEmpty).length;
    final rawMs = words * msPerWord;
    final ms = rawMs < floor.inMilliseconds ? floor.inMilliseconds.toDouble() : rawMs;
    final capped = ms > cap.inMilliseconds ? cap.inMilliseconds.toDouble() : ms;
    return Duration(milliseconds: capped.round());
  }
}

/// Drives one [PacedCodeReveal]'s auto-advance state from outside the
/// widget tree — lets the parent screen pause a specific code card (e.g.
/// while a text-suggestion overlay is interrupting it) without the parent
/// needing to know anything about line indices or timers.
class PacedCodeRevealController extends ChangeNotifier {
  bool _pausedByOverlay = false;
  bool get pausedByOverlay => _pausedByOverlay;

  bool manuallyOverridden = false;
  bool isFinished = false;

  Timer? _resumeTimer;

  /// Pause auto-advance for roughly [overlayDuration] (matched to however
  /// long the interrupting overlay is expected to be on screen), then hold
  /// an additional fixed [resumeGraceBuffer] before resuming — a small,
  /// constant re-orientation cost for "look back down, re-find my place"
  /// that doesn't scale with how long the interruption was.
  void pauseForOverlay(
    Duration overlayDuration, {
    Duration resumeGraceBuffer = const Duration(milliseconds: 1200),
  }) {
    _resumeTimer?.cancel();
    _pausedByOverlay = true;
    notifyListeners();
    _resumeTimer = Timer(overlayDuration + resumeGraceBuffer, () {
      _pausedByOverlay = false;
      notifyListeners();
    });
  }

  void markManuallyOverridden() {
    manuallyOverridden = true;
    notifyListeners();
  }

  void resumeAuto() {
    manuallyOverridden = false;
    notifyListeners();
  }

  void markFinished() {
    isFinished = true;
    notifyListeners();
  }

  @override
  void dispose() {
    _resumeTimer?.cancel();
    super.dispose();
  }
}

/// Stepped, paced, no-touch code reveal. One physical line active +
/// visible lookahead below it (karaoke-style anchor), jump-cut (not
/// smooth-scrolled) between lines, each line's hold duration from
/// [LinePacing]. A tap pauses auto-advance entirely (manual override) —
/// the small "resume auto" affordance is the one deliberate touch
/// exception, not continuous scrolling.
class PacedCodeReveal extends StatefulWidget {
  const PacedCodeReveal({
    super.key,
    required this.code,
    required this.controller,
    this.lookaheadLines = 2,
  });

  final String code;
  final PacedCodeRevealController controller;
  final int lookaheadLines;

  @override
  State<PacedCodeReveal> createState() => _PacedCodeRevealState();
}

class _PacedCodeRevealState extends State<PacedCodeReveal> {
  late final List<String> _lines;
  int _index = 0;
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _lines = widget.code.split('\n');
    widget.controller.addListener(_onControllerChanged);
    _scheduleNext();
  }

  @override
  void dispose() {
    _timer?.cancel();
    widget.controller.removeListener(_onControllerChanged);
    super.dispose();
  }

  bool get _autoAdvancing =>
      !widget.controller.manuallyOverridden &&
      !widget.controller.pausedByOverlay &&
      !widget.controller.isFinished;

  void _onControllerChanged() {
    if (!mounted) return;
    if (_autoAdvancing && (_timer == null || !_timer!.isActive)) {
      _scheduleNext();
    } else if (!_autoAdvancing) {
      _timer?.cancel();
    }
  }

  void _scheduleNext() {
    _timer?.cancel();
    if (!_autoAdvancing) return;
    if (_index >= _lines.length - 1) {
      widget.controller.markFinished();
      return;
    }
    final duration = LinePacing.forLine(_lines[_index]);
    _timer = Timer(duration, () {
      if (!mounted || !_autoAdvancing) return;
      setState(() => _index++);
      _scheduleNext();
    });
  }

  void _handleManualTap() {
    widget.controller.markManuallyOverridden();
  }

  void _handleResumeAuto() {
    widget.controller.resumeAuto();
    _scheduleNext();
  }

  @override
  Widget build(BuildContext context) {
    final start = _index;
    final end = (start + 1 + widget.lookaheadLines).clamp(0, _lines.length);
    final visible = _lines.sublist(start, end);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        GestureDetector(
          onTap: _handleManualTap,
          behavior: HitTestBehavior.opaque,
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
            decoration: BoxDecoration(
              color: Colors.black.withValues(alpha: 0.18),
              borderRadius: BorderRadius.circular(6),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                for (var i = 0; i < visible.length; i++)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 1),
                    child: Text(
                      visible[i].isEmpty ? ' ' : visible[i],
                      style: TextStyle(
                        fontFamily: 'monospace',
                        fontSize: 12.5,
                        height: 1.45,
                        // Active (first visible) line stays anchored/
                        // highlighted so a jump-cut doesn't disorient, and
                        // so returning from a paused/overlay state lands
                        // the eye right back where it was.
                        color: i == 0
                            ? Colors.white
                            : Colors.white.withValues(alpha: 0.38),
                        fontWeight: i == 0 ? FontWeight.w600 : FontWeight.normal,
                      ),
                    ),
                  ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 6),
        if (widget.controller.isFinished)
          const Text(
            'Done — full snippet above.',
            style: TextStyle(fontSize: 10.5, color: Colors.white54),
          )
        else if (widget.controller.pausedByOverlay)
          const Text(
            'Paused for incoming suggestion…',
            style: TextStyle(fontSize: 10.5, color: Colors.white54),
          )
        else if (widget.controller.manuallyOverridden)
          GestureDetector(
            onTap: _handleResumeAuto,
            child: const Text(
              'Auto-advance paused — tap to resume',
              style: TextStyle(
                fontSize: 10.5,
                color: Colors.white70,
                decoration: TextDecoration.underline,
              ),
            ),
          )
        else
          Text(
            'Line ${_index + 1} of ${_lines.length} — auto-advancing',
            style: const TextStyle(fontSize: 10.5, color: Colors.white38),
          ),
      ],
    );
  }
}
