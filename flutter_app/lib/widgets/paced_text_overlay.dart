// Phase 4 (2026-08-16) — paced text-interruption overlay.
//
// Design record: build plan §11b. Shown when a text-type ("read and
// you're done") suggestion arrives while the user is mid-copy on a code
// card. Semi-transparent (code still faintly visible underneath —
// preserves orientation rather than a hard cut away), sized to its own
// estimated read time via ReadPacing, self-dismissing. The code card's
// own [PacedCodeRevealController] is paused for a proportional duration
// by the caller (see live_session_screen.dart) — this widget only
// renders the interruption itself, it doesn't own that pause logic.

import 'dart:async';
import 'package:flutter/material.dart';

import 'paced_code_reveal.dart' show ReadPacing;

class PacedTextOverlay extends StatefulWidget {
  const PacedTextOverlay({
    super.key,
    required this.text,
    required this.onDismiss,
  });

  final String text;
  final VoidCallback onDismiss;

  @override
  State<PacedTextOverlay> createState() => _PacedTextOverlayState();
}

class _PacedTextOverlayState extends State<PacedTextOverlay> {
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    final duration = ReadPacing.forText(widget.text);
    _timer = Timer(duration, () {
      if (mounted) widget.onDismiss();
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    // Semi-transparent by design (not fully opaque) — a deliberate choice
    // from the design discussion: the code stays faintly visible behind
    // this so the user doesn't lose all sense of where they were.
    return Positioned.fill(
      child: Container(
        decoration: BoxDecoration(
          color: Colors.black.withValues(alpha: 0.72),
          borderRadius: BorderRadius.circular(8),
        ),
        padding: const EdgeInsets.all(14),
        alignment: Alignment.center,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text(
              'NEW SUGGESTION',
              style: TextStyle(
                fontSize: 10,
                letterSpacing: 0.6,
                color: Colors.white60,
              ),
            ),
            const SizedBox(height: 6),
            Text(
              widget.text,
              textAlign: TextAlign.center,
              style: const TextStyle(fontSize: 14, height: 1.4, color: Colors.white),
            ),
          ],
        ),
      ),
    );
  }
}
