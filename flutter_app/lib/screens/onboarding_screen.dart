import 'package:flutter/material.dart';

import '../theme.dart';

/// First-run experience: welcome -> mic permission pre-prompt -> ready.
/// Matches design/mockups/onboarding_mockup.html. Mic permission is not
/// actually requested here yet (no permission_handler dependency in
/// pubspec.yaml) — the "Allow microphone access" button just advances the
/// flow for now. Wiring the real OS permission dialog is a follow-up once
/// a permissions package is added; the pre-prompt UI/copy is final.
class OnboardingScreen extends StatefulWidget {
  const OnboardingScreen({super.key, required this.onDone});

  final VoidCallback onDone;

  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  final _controller = PageController();
  int _step = 0;

  void _next() {
    if (_step == 2) {
      widget.onDone();
      return;
    }
    _controller.nextPage(
      duration: const Duration(milliseconds: 250),
      curve: Curves.easeOut,
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: CopilotColors.ink,
      body: SafeArea(
        child: Column(
          children: [
            Align(
              alignment: Alignment.topRight,
              child: Padding(
                padding: const EdgeInsets.only(right: 20, top: 8),
                child: TextButton(
                  onPressed: widget.onDone,
                  child: const Text(
                    'Skip',
                    style: TextStyle(
                      fontFamily: CopilotFonts.mono,
                      fontSize: 11,
                      color: CopilotColors.mist,
                    ),
                  ),
                ),
              ),
            ),
            _ProgressDots(step: _step, count: 3),
            Expanded(
              child: PageView(
                controller: _controller,
                physics: const NeverScrollableScrollPhysics(),
                onPageChanged: (i) => setState(() => _step = i),
                children: const [
                  _WelcomeStep(),
                  _MicPermissionStep(),
                  _ReadyStep(),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(24, 8, 24, 24),
              child: SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: _next,
                  child: Text(switch (_step) {
                    0 => 'Continue',
                    1 => 'Allow microphone access',
                    _ => 'Go to Home',
                  }),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ProgressDots extends StatelessWidget {
  const _ProgressDots({required this.step, required this.count});

  final int step;
  final int count;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.center,
        children: List.generate(count, (i) {
          final active = i == step;
          return Container(
            margin: const EdgeInsets.symmetric(horizontal: 3),
            width: 20,
            height: 3,
            decoration: BoxDecoration(
              color: active ? CopilotColors.amber : CopilotColors.panelRaised,
              borderRadius: BorderRadius.circular(2),
            ),
          );
        }),
      ),
    );
  }
}

class _StepScaffold extends StatelessWidget {
  const _StepScaffold({
    required this.hero,
    required this.eyebrow,
    required this.title,
    required this.description,
    this.extra,
  });

  final Widget hero;
  final String eyebrow;
  final Widget title;
  final String description;
  final Widget? extra;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 28),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          hero,
          Text(
            eyebrow.toUpperCase(),
            style: const TextStyle(
              fontFamily: CopilotFonts.mono,
              fontSize: 10.5,
              letterSpacing: 1.5,
              color: CopilotColors.mist,
            ),
          ),
          const SizedBox(height: 14),
          DefaultTextStyle(
            style: const TextStyle(
              fontFamily: CopilotFonts.display,
              fontWeight: FontWeight.w600,
              fontSize: 23,
              height: 1.3,
              color: CopilotColors.paper,
            ),
            textAlign: TextAlign.center,
            child: title,
          ),
          const SizedBox(height: 12),
          Text(
            description,
            textAlign: TextAlign.center,
            style: const TextStyle(
              fontSize: 13.5,
              height: 1.55,
              color: CopilotColors.mist,
            ),
          ),
          if (extra != null) ...[const SizedBox(height: 18), extra!],
        ],
      ),
    );
  }
}

class _WelcomeStep extends StatelessWidget {
  const _WelcomeStep();

  @override
  Widget build(BuildContext context) {
    return _StepScaffold(
      hero: Container(
        width: 168,
        height: 168,
        margin: const EdgeInsets.only(bottom: 28),
        child: CustomPaint(
          painter: _TickRingPainter(litFraction: 1.0, static: true),
          child: Center(
            child: Container(
              width: 78,
              height: 78,
              decoration: const BoxDecoration(
                color: CopilotColors.panel,
                shape: BoxShape.circle,
              ),
              child: const Icon(Icons.mic_none, color: CopilotColors.amber, size: 30),
            ),
          ),
        ),
      ),
      eyebrow: 'Conversation Copilot',
      title: const Text.rich(
        TextSpan(children: [
          TextSpan(text: 'A quiet '),
          TextSpan(text: 'second brain', style: TextStyle(color: CopilotColors.amber)),
          TextSpan(text: '\nfor live conversations'),
        ]),
      ),
      description: 'Interviews, sales calls, technical discussions — Copilot '
          'listens, transcribes, and surfaces a suggestion right when you '
          'need one. Built to be glanced at for a second, then get out of '
          'your way.',
    );
  }
}

class _MicPermissionStep extends StatelessWidget {
  const _MicPermissionStep();

  @override
  Widget build(BuildContext context) {
    return _StepScaffold(
      hero: Container(
        width: 96,
        height: 96,
        margin: const EdgeInsets.only(bottom: 24),
        decoration: BoxDecoration(
          color: CopilotColors.panel,
          shape: BoxShape.circle,
          border: Border.all(color: CopilotColors.hairline),
        ),
        child: const Icon(Icons.mic_none, color: CopilotColors.amber, size: 36),
      ),
      eyebrow: 'Before we ask Android',
      title: const Text.rich(
        TextSpan(children: [
          TextSpan(text: 'Copilot needs your '),
          TextSpan(text: 'microphone', style: TextStyle(color: CopilotColors.amber)),
        ]),
      ),
      description: 'This is the only permission the app needs. Audio is '
          'processed to power live transcription and suggestions — nothing '
          'is recorded without you starting a session.',
      extra: Container(
        width: double.infinity,
        padding: const EdgeInsets.all(18),
        decoration: BoxDecoration(
          color: CopilotColors.panel,
          borderRadius: BorderRadius.circular(14),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              width: 38,
              height: 38,
              decoration: BoxDecoration(
                color: CopilotColors.amberDim,
                borderRadius: BorderRadius.circular(10),
              ),
              child: const Icon(Icons.shield_outlined, color: CopilotColors.amber, size: 18),
            ),
            const SizedBox(width: 12),
            const Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Only active during a session',
                    style: TextStyle(fontSize: 13, color: CopilotColors.paper),
                  ),
                  SizedBox(height: 3),
                  Text(
                    'The mic is off the moment you\'re not in a live session — '
                    'no background listening.',
                    style: TextStyle(fontSize: 12, height: 1.5, color: CopilotColors.mist),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ReadyStep extends StatelessWidget {
  const _ReadyStep();

  @override
  Widget build(BuildContext context) {
    return _StepScaffold(
      hero: Container(
        width: 120,
        height: 120,
        margin: const EdgeInsets.only(bottom: 26),
        decoration: BoxDecoration(
          color: CopilotColors.panel,
          shape: BoxShape.circle,
          border: Border.all(color: CopilotColors.amber, width: 2),
        ),
        child: const Icon(Icons.mic_none, color: CopilotColors.amber, size: 40),
      ),
      eyebrow: "You're set up",
      title: const Text.rich(
        TextSpan(children: [
          TextSpan(text: 'Ready for your '),
          TextSpan(text: 'first session', style: TextStyle(color: CopilotColors.amber)),
        ]),
      ),
      description: 'Tap the ringed button on Home any time to start '
          'listening. Suggestions appear as blue cards next to the live '
          'transcript.',
      extra: Container(
        width: double.infinity,
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        decoration: BoxDecoration(
          color: CopilotColors.panel,
          borderRadius: BorderRadius.circular(10),
        ),
        child: const Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(Icons.info_outline, color: CopilotColors.blue, size: 15),
            SizedBox(width: 10),
            Expanded(
              child: Text(
                'Try a low-stakes call first — a quick sync or a friend — '
                'to get a feel for the timing before something high-stakes.',
                style: TextStyle(fontSize: 12, height: 1.5, color: CopilotColors.mist),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Segmented amplitude-ring, the app's signature motif (see build plan
/// Phase 3.5). `litFraction` is 0..1 of ticks lit amber; `static: true`
/// renders the welcome-step ring with the alternating dim/lit look from
/// the mockup rather than reacting to live audio (that only happens on
/// the actual Live Session screen).
class _TickRingPainter extends CustomPainter {
  _TickRingPainter({required this.litFraction, this.static = false});

  final double litFraction;
  final bool static;

  @override
  void paint(Canvas canvas, Size size) {
    final center = size.center(Offset.zero);
    final radius = size.width / 2 - 6;
    const tickCount = 24;
    final basePaint = Paint()
      ..color = CopilotColors.panelRaised
      ..strokeWidth = 2
      ..strokeCap = StrokeCap.round;
    final litPaint = Paint()
      ..color = CopilotColors.amber
      ..strokeWidth = 3
      ..strokeCap = StrokeCap.round;
    final dimLitPaint = Paint()
      ..color = CopilotColors.amberDim
      ..strokeWidth = 3
      ..strokeCap = StrokeCap.round;

    canvas.drawCircle(
      center,
      radius,
      Paint()
        ..color = CopilotColors.panelRaised
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2,
    );

    for (var i = 0; i < tickCount; i++) {
      final angle = (i / tickCount) * 2 * 3.14159265 - 3.14159265 / 2;
      final outer = center + Offset.fromDirection(angle, radius + 6);
      final inner = center + Offset.fromDirection(angle, radius - 4);
      final isCardinal = i % (tickCount ~/ 8) == 0;
      Paint paint;
      if (!isCardinal) {
        paint = basePaint;
      } else if (static) {
        // Alternate lit/dim-lit on the welcome step, matching the mockup's
        // static hero ring exactly.
        paint = (i ~/ (tickCount ~/ 8)).isEven ? litPaint : dimLitPaint;
      } else {
        final ticksLit = (8 * litFraction).round();
        paint = (i ~/ (tickCount ~/ 8)) < ticksLit ? litPaint : basePaint;
      }
      canvas.drawLine(inner, outer, paint);
    }
  }

  @override
  bool shouldRepaint(covariant _TickRingPainter oldDelegate) =>
      oldDelegate.litFraction != litFraction || oldDelegate.static != static;
}
