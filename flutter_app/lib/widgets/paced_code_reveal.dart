// Phase 4 (2026-08-16 → 2026-08-17) — smart, paced, no-touch code reveal.
//
// Design record: build plan §11b (original stepped-line design) + §13
// (this rewrite — real-time typing simulation). Built from a real,
// explicitly stated use case: the person reading this card is
// hand-typing the code into a *separate* device (not clipboard copy) and
// cannot touch the phone while doing it.
//
// v1 of this widget (§12b) revealed one whole line at a time, held for a
// flat duration, then jump-cut to the next. Ed's follow-up feedback: that
// read as "slow enough to read, not slow enough to hand-copy," and didn't
// feel like watching an actual developer work. This rewrite replaces the
// per-line hold with a character-by-character typing simulation —
// variable rhythm (fast through whitespace/punctuation, slower through
// identifiers, a breath-pause after `{`/`:`/`,`, a longer think-pause
// before a new line), plus real typo-then-backspace-then-correct
// mistakes on identifier characters, per Ed's explicit instruction
// ("include the typo, the correction... as real as possible").
//
// One open design question from the discussion was never answered before
// the session moved to logs/implementation (Q: keep the dimmed lookahead
// preview now that a live cursor exists, or drop it?). Decision made
// here, flagged rather than silently assumed: the lookahead is DROPPED.
// Two reasons: (1) a live per-character cursor already does the
// "anchoring" job the lookahead was originally there for, so it's
// redundant; (2) revealing upcoming lines ahead of the typing cursor
// would let a fast reader jump ahead of the pace the whole feature
// exists to slow down to, which fights the actual goal (give hands-typing
// time). If this was wrong, it's a one-line change to reintroduce a
// dimmed preview of buffered-but-not-yet-typed lines.
//
// Overflow handling also changes shape: v1 solved overflow by only ever
// showing 1 active + N lookahead lines. That doesn't fit "watch a
// developer type in their editor" — real editors accumulate text. So
// this version uses a fixed-height scrolling viewport instead: typed
// lines accumulate like a real editor buffer, and the view jump-scrolls
// (never smooth/animated — consistent with the original "no continuous
// scroll" decision) to keep the active typing position in view. Already-
// typed lines scroll off the top exactly the way a terminal or a tall
// file does in a short editor pane, not by hiding future content.
//
// This file is a standalone, non-Flutter-toolchain-verified addition
// (same caveat as every other Flutter change in this build plan) —
// checked by hand for brace/paren balance and Dart syntax, not run
// through `flutter analyze` or a real build.

import 'dart:async';
import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart' show ScrollDirection;

/// Estimated read time for prose (used by the interrupting text overlay,
/// not the code-typing path below) — "how long does a human need to
/// read this", tuned for reading rather than hand-typing.
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

/// One step in a precomputed typing "script": the full buffer text at
/// this point in time, and how long to wait (after the previous step)
/// before this state becomes visible. Precomputing the whole script up
/// front (rather than deciding rhythm live, char by char) keeps the
/// widget's timer loop dead simple — it's just walking a list — and
/// makes the rhythm logic trivially unit-testable in isolation later.
class _TypeStep {
  const _TypeStep(this.text, this.delay);
  final String text;
  final Duration delay;
}

/// QWERTY-adjacent keys per letter, for plausible (not random-anywhere-
/// on-the-keyboard) typo substitutions. Deliberately a rough/approximate
/// map, not a full physical-keyboard model — good enough to look like a
/// real mistake ("teh" not "txw").
const Map<String, String> _qwertyNeighbors = {
  'q': 'wa', 'w': 'qes', 'e': 'wrd', 'r': 'etf', 't': 'ryg', 'y': 'tuh',
  'u': 'yij', 'i': 'uok', 'o': 'ipl', 'p': 'ol',
  'a': 'qsz', 's': 'awdz', 'd': 'sefx', 'f': 'drtgc', 'g': 'fthv',
  'h': 'gyjb', 'j': 'huk', 'k': 'jil', 'l': 'ko',
  'z': 'asx', 'x': 'zsdc', 'c': 'xdfv', 'v': 'cfgb', 'b': 'vghn',
  'n': 'bhjm', 'm': 'njk',
};

String _typoFor(String ch, math.Random rng) {
  final lower = ch.toLowerCase();
  final neighbors = _qwertyNeighbors[lower];
  var result = (neighbors != null && neighbors.isNotEmpty)
      ? neighbors[rng.nextInt(neighbors.length)]
      : String.fromCharCode(97 + rng.nextInt(26));
  if (ch != lower) result = result.toUpperCase();
  return result;
}

double _jitterRange(math.Random rng, num lo, num hi) =>
    lo + rng.nextDouble() * (hi - lo);

/// Builds the full character-by-character typing script for [code]:
/// variable per-character rhythm, breath-pauses after "opening"
/// punctuation, a think-pause before each new line, and probabilistic
/// typo-then-backspace-then-correct sequences on identifier characters.
List<_TypeStep> buildTypingScript(String code, {int? seed}) {
  final rng = math.Random(seed);
  final steps = <_TypeStep>[];
  final buffer = StringBuffer();
  var pendingExtraMs = 0.0;

  double charDelayMs(String ch, bool isSpace) {
    if (isSpace) return _jitterRange(rng, 8, 22); // whitespace/indent: fast, no thought needed
    if (RegExp(r'[A-Za-z_]').hasMatch(ch)) {
      return _jitterRange(rng, 60, 130); // identifier letters: naming takes a beat
    }
    if (RegExp(r'[0-9]').hasMatch(ch)) return _jitterRange(rng, 35, 75);
    return _jitterRange(rng, 28, 65); // other punctuation/operators
  }

  void pushStep(double extraMs) {
    steps.add(_TypeStep(
      buffer.toString(),
      Duration(milliseconds: (pendingExtraMs + extraMs).round()),
    ));
    pendingExtraMs = 0;
  }

  final lines = code.split('\n');
  for (var li = 0; li < lines.length; li++) {
    final line = lines[li];

    if (li > 0) {
      buffer.write('\n');
      pushStep(_jitterRange(rng, 70, 130)); // pressing Enter
      final trimmed = line.trim();
      // Longer "find my place, decide what's next" pause before a new
      // line's first character — reuses the original design's "find my
      // place" floor, just relocated to the front of the line instead of
      // baked into a flat post-line hold. Blank lines get a shorter beat.
      pendingExtraMs = trimmed.isEmpty
          ? _jitterRange(rng, 180, 340)
          : _jitterRange(rng, 480, 850);
    }

    var identStreak = 0;
    for (var i = 0; i < line.length; i++) {
      final ch = line[i];
      final isSpace = ch == ' ' || ch == '\t';
      final isIdentChar = RegExp(r'[A-Za-z0-9_]').hasMatch(ch);
      identStreak = isIdentChar ? identStreak + 1 : 0;

      // Typos only on identifier characters, and only once a real word
      // has started (streak > 2) — a mistake on the very first letter of
      // a name reads as noise, not realism. Kept intentionally
      // infrequent: this simulates a careful-ish developer, not someone
      // who can't type.
      final doTypo = isIdentChar &&
          identStreak > 2 &&
          RegExp(r'[A-Za-z]').hasMatch(ch) &&
          rng.nextDouble() < 0.045;

      if (doTypo) {
        buffer.write(_typoFor(ch, rng));
        pushStep(charDelayMs(ch, isSpace));
        pushStep(_jitterRange(rng, 220, 420)); // notice the mistake
        final s = buffer.toString();
        buffer
          ..clear()
          ..write(s.substring(0, s.length - 1)); // backspace
        pushStep(_jitterRange(rng, 70, 140));
      }

      buffer.write(ch);
      pushStep(charDelayMs(ch, isSpace));

      if ('{:,('.contains(ch)) {
        // Breath-point: a beat before deciding what comes next, mirrored
        // as extra delay on the NEXT character rather than this one.
        pendingExtraMs = _jitterRange(rng, 160, 320);
      }
    }
  }

  return steps;
}

/// Drives one [PacedCodeReveal]'s typing simulation from outside the
/// widget tree — lets the parent screen pause a specific code card (e.g.
/// while a text-suggestion overlay is interrupting it) without the
/// parent needing to know anything about the typing script internals.
/// Public API unchanged from the v1 (line-stepped) controller so
/// `live_session_screen.dart`'s wiring didn't need to change.
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

/// Real-time, character-by-character typing simulation of [code] — fast
/// through whitespace/punctuation, slower and pausier through
/// identifiers, occasional realistic typo-then-backspace-then-correct
/// sequences, a think-pause before each new line. Renders in a fixed-
/// height, jump-scrolling (never smooth-animated) viewport so a long
/// snippet never overflows the card regardless of length — already-typed
/// lines scroll off the top like a real editor/terminal pane, rather
/// than the whole answer ever being visible at once.
///
/// A tap pauses the simulation entirely (manual override) — the one
/// deliberate touch exception, same as v1, not continuous scrolling.
class PacedCodeReveal extends StatefulWidget {
  const PacedCodeReveal({
    super.key,
    required this.code,
    required this.controller,
    this.viewportHeight = 252, // was 210 in §13's original write-up — bumped ~20% for readability; see §14
  });

  final String code;
  final PacedCodeRevealController controller;
  final double viewportHeight;

  @override
  State<PacedCodeReveal> createState() => _PacedCodeRevealState();
}

class _PacedCodeRevealState extends State<PacedCodeReveal> {
  late final List<_TypeStep> _script;
  final ScrollController _scrollCtl = ScrollController();

  int _stepIndex = 0;
  String _displayText = '';
  Timer? _timer;

  bool _cursorOn = true;
  Timer? _cursorBlinkTimer;

  // Whether the view should keep auto-jumping to the newest typed
  // character. Starts true (follow the cursor, as before). Turns false
  // the moment the person drags the snippet themselves — e.g. scrolling
  // back up to re-read the start while glancing between devices — so the
  // next typed character doesn't yank them back down. Turns true again
  // once they scroll back down to the bottom themselves, or when they
  // tap the "jump to latest" affordance.
  bool _followBottom = true;

  @override
  void initState() {
    super.initState();
    _script = buildTypingScript(widget.code);
    widget.controller.addListener(_onControllerChanged);
    _cursorBlinkTimer = Timer.periodic(const Duration(milliseconds: 500), (_) {
      if (!mounted) return;
      setState(() => _cursorOn = !_cursorOn);
    });
    _scheduleNext();
  }

  @override
  void dispose() {
    _timer?.cancel();
    _cursorBlinkTimer?.cancel();
    widget.controller.removeListener(_onControllerChanged);
    _scrollCtl.dispose();
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
    if (_stepIndex >= _script.length) {
      widget.controller.markFinished();
      return;
    }
    final step = _script[_stepIndex];
    _timer = Timer(step.delay, () {
      if (!mounted || !_autoAdvancing) return;
      setState(() => _displayText = step.text);
      _stepIndex++;
      _snapScrollToBottom();
      _scheduleNext();
    });
  }

  // Jump (never animate) the viewport to the bottom so the active typing
  // position always stays visible — mirrors how a terminal or a short
  // editor pane naturally follows the cursor. Deliberately jumpTo, not
  // animateTo: a moving/gliding scroll was already explicitly rejected
  // for this feature (fights "hold still while I type this").
  //
  // Only runs while _followBottom is true, so it never fights a manual
  // scroll the person is mid-way through (see _followBottom above).
  void _snapScrollToBottom() {
    if (!_followBottom) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scrollCtl.hasClients) return;
      _scrollCtl.jumpTo(_scrollCtl.position.maxScrollExtent);
    });
  }

  bool _isAtBottom(ScrollMetrics m) => m.pixels >= m.maxScrollExtent - 4;

  void _jumpToLatest() {
    setState(() => _followBottom = true);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scrollCtl.hasClients) return;
      _scrollCtl.jumpTo(_scrollCtl.position.maxScrollExtent);
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
    final showCursor = !widget.controller.isFinished && _cursorOn;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        GestureDetector(
          onTap: _handleManualTap,
          behavior: HitTestBehavior.opaque,
          child: Container(
            height: widget.viewportHeight,
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
            decoration: BoxDecoration(
              color: Colors.black.withValues(alpha: 0.18),
              borderRadius: BorderRadius.circular(6),
            ),
            child: NotificationListener<ScrollNotification>(
              // Manual drag anywhere in the viewport (scrolling back up to
              // re-read the start, etc.) suspends auto-follow so the next
              // typed character doesn't yank the view back down. Dragging
              // back down to the bottom hands auto-follow back.
              onNotification: (notification) {
                if (notification is UserScrollNotification &&
                    notification.direction != ScrollDirection.idle) {
                  if (_followBottom) setState(() => _followBottom = false);
                } else if (notification is ScrollEndNotification &&
                    !_followBottom &&
                    _isAtBottom(notification.metrics)) {
                  setState(() => _followBottom = true);
                }
                return false;
              },
              child: SingleChildScrollView(
                controller: _scrollCtl,
                physics: const ClampingScrollPhysics(),
                child: RichText(
                  text: TextSpan(
                    style: const TextStyle(
                      fontFamily: 'monospace',
                      fontSize: 12.5,
                      height: 1.45,
                      color: Colors.white,
                    ),
                    children: [
                      TextSpan(text: _displayText),
                      TextSpan(
                        text: '▌',
                        style: TextStyle(
                          color: showCursor ? Colors.white : Colors.transparent,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
        if (!_followBottom)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: GestureDetector(
              onTap: _jumpToLatest,
              child: const Text(
                '↓ Jump to latest',
                style: TextStyle(
                  fontSize: 10.5,
                  color: Colors.white70,
                  decoration: TextDecoration.underline,
                ),
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
          const Text(
            'Typing…',
            style: TextStyle(fontSize: 10.5, color: Colors.white38),
          ),
      ],
    );
  }
}
