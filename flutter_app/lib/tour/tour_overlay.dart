import 'package:flutter/material.dart';

import '../theme.dart';
import 'tour_controller.dart';
import 'tour_step.dart';

/// Full-app hint system (2026-08-31, build plan §16): the actual
/// spotlight rendering. No third-party showcase package — hand-rolled
/// deliberately, because the mandatory task-gating behavior (Lewis: "it
/// shouldn't be able to do anything there until it completes the
/// onboarding" / "component tips + a short task before advancing") isn't
/// what off-the-shelf showcase packages are built for, and getting a
/// third-party package's exact current API right with no Flutter
/// toolchain available in this sandbox to compile against was a real
/// risk not worth taking for what's really just "dim the screen, cut a
/// hole, put a card near it" — see the build plan entry for the full
/// reasoning.
///
/// Wrap this around the widget tree that should be tourable (see
/// main.dart's AppShell) — it listens to [controller] and paints nothing
/// when [TourController.isActive] is false, so it's cheap to leave
/// mounted everywhere rather than conditionally inserting/removing it.
class TourOverlay extends StatefulWidget {
  const TourOverlay({super.key, required this.controller, required this.child});

  final TourController controller;
  final Widget child;

  @override
  State<TourOverlay> createState() => _TourOverlayState();
}

class _TourOverlayState extends State<TourOverlay> {
  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_onTourChanged);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onTourChanged);
    super.dispose();
  }

  void _onTourChanged() {
    if (mounted) setState(() {});
  }

  Rect? _targetRect(GlobalKey key) {
    final renderObject = key.currentContext?.findRenderObject();
    if (renderObject is! RenderBox || !renderObject.attached) return null;
    final topLeft = renderObject.localToGlobal(Offset.zero);
    return topLeft & renderObject.size;
  }

  @override
  Widget build(BuildContext context) {
    final controller = widget.controller;
    final step = controller.isActive ? controller.currentStep : null;

    if (step == null) {
      // Either the tour isn't active, or we've walked past the last
      // screen's last step (the frame before the controller's own
      // "advance to next screen" callback swaps the visible AppShell
      // tab) — either way, nothing to paint, and critically the barrier
      // must NOT block input in this state, or the app would freeze
      // between tour screens.
      return widget.child;
    }

    // Rect resolved from the CURRENT frame's layout. If the target
    // widget isn't mounted yet (e.g. this tour step targets a screen
    // AppShell hasn't switched to on-screen yet), rect is null and only
    // a dimmed barrier + the tip card (undocked, centered) render —
    // still blocks interaction correctly, just without a spotlight
    // cutout until the target actually lays out. A post-frame callback
    // schedules one more rebuild so the cutout appears as soon as it can
    // without the caller having to manage that timing itself.
    final rect = _targetRect(step.key);
    if (rect == null) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) setState(() {});
      });
    }

    return Stack(
      children: [
        widget.child,
        // Blocking barrier (Lewis: "shouldn't be able to do anything
        // there until it completes"). For a plain step this is one
        // full-screen absorber. For a TASK-gated step (requiresTask),
        // the whole point is that the user must interact with the real
        // target widget to advance — so the barrier is built from up to
        // four bands AROUND the spotlighted rect instead of one
        // full-screen absorber, leaving a real gap with nothing painted
        // over it there at all. A gap (not just a "let taps pass
        // through" flag on a full-screen detector) is deliberate: it's
        // the simplest way to guarantee the real widget underneath gets
        // the actual tap event Flutter's hit-testing would otherwise
        // route to this overlay first.
        if (step.requiresTask && rect != null)
          ..._bandsAround(rect, context)
        else
          Positioned.fill(
            child: GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: () {},
              child: CustomPaint(
                painter: _SpotlightPainter(rect: rect),
                size: Size.infinite,
              ),
            ),
          ),
        if (step.requiresTask && rect != null)
          // The painter still needs to run somewhere to draw the dim +
          // cutout visuals even though hit-testing is now handled by the
          // bands above — IgnorePointer so this purely-visual layer
          // never competes with either the bands or the real target
          // underneath for the tap.
          IgnorePointer(
            child: CustomPaint(painter: _SpotlightPainter(rect: rect), size: Size.infinite),
          ),
        if (rect != null) _TipCard(rect: rect, step: step, controller: controller),
        if (rect == null) Center(child: _TipCard(rect: null, step: step, controller: controller)),
      ],
    );
  }

  /// Four bands (top/bottom/left/right) that together cover the whole
  /// screen minus [rect] — used instead of a single full-screen absorber
  /// when the current step needs the real widget under [rect] to
  /// actually receive the tap (see the requiresTask branch in build()
  /// above). Each band is a plain opaque GestureDetector with an empty
  /// onTap, same absorb-everything behavior as the normal barrier, just
  /// tiled around a rectangular hole instead of covering the hole too.
  List<Widget> _bandsAround(Rect rect, BuildContext context) {
    final screenSize = MediaQuery.of(context).size;
    Widget band(double left, double top, double width, double height) {
      if (width <= 0 || height <= 0) return const SizedBox.shrink();
      return Positioned(
        left: left,
        top: top,
        width: width,
        height: height,
        child: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTap: () {},
        ),
      );
    }

    return [
      band(0, 0, screenSize.width, rect.top), // above
      band(0, rect.bottom, screenSize.width, screenSize.height - rect.bottom), // below
      band(0, rect.top, rect.left, rect.height), // left of hole, same row
      band(rect.right, rect.top, screenSize.width - rect.right, rect.height), // right of hole, same row
    ];
  }
}

class _SpotlightPainter extends CustomPainter {
  _SpotlightPainter({required this.rect});

  final Rect? rect;
  static const _padding = 8.0;

  @override
  void paint(Canvas canvas, Size size) {
    final barrier = Paint()..color = CopilotColors.ink.withValues(alpha: 0.82);
    final fullScreen = Path()..addRect(Offset.zero & size);

    if (rect == null) {
      canvas.drawPath(fullScreen, barrier);
      return;
    }

    final hole = rect!.inflate(_padding);
    final holePath = Path()
      ..addRRect(RRect.fromRectAndRadius(hole, const Radius.circular(14)));
    final withHole = Path.combine(PathOperation.difference, fullScreen, holePath);
    canvas.drawPath(withHole, barrier);

    // Amber outline around the spotlight, matching the app's existing
    // accent-border convention (e.g. home_screen.dart's Start Session
    // ring uses the same CopilotColors.amber border treatment).
    canvas.drawRRect(
      RRect.fromRectAndRadius(hole, const Radius.circular(14)),
      Paint()
        ..color = CopilotColors.amber
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2,
    );
  }

  @override
  bool shouldRepaint(covariant _SpotlightPainter oldDelegate) => oldDelegate.rect != rect;
}

class _TipCard extends StatelessWidget {
  const _TipCard({required this.rect, required this.step, required this.controller});

  final Rect? rect;
  final TourStep step;
  final TourController controller;

  @override
  Widget build(BuildContext context) {
    final screenSize = MediaQuery.of(context).size;
    const cardWidth = 280.0;
    const margin = 16.0;

    final card = Container(
      width: cardWidth,
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: CopilotColors.panel,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: CopilotColors.hairline),
        boxShadow: const [BoxShadow(color: Colors.black45, blurRadius: 18, offset: Offset(0, 6))],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            step.title,
            style: const TextStyle(
              fontFamily: CopilotFonts.display,
              fontWeight: FontWeight.w600,
              fontSize: 14.5,
              color: CopilotColors.paper,
            ),
          ),
          const SizedBox(height: 8),
          Text(
            step.description,
            style: const TextStyle(fontSize: 12.5, color: CopilotColors.mist, height: 1.5),
          ),
          const SizedBox(height: 14),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                step.requiresTask ? 'TRY IT TO CONTINUE' : '',
                style: const TextStyle(
                  fontFamily: CopilotFonts.mono,
                  fontSize: 9.5,
                  letterSpacing: 1,
                  color: CopilotColors.amber,
                ),
              ),
              if (!step.requiresTask)
                TextButton(
                  onPressed: controller.advance,
                  child: const Text(
                    'Next',
                    style: TextStyle(fontFamily: CopilotFonts.mono, fontSize: 12, color: CopilotColors.amber),
                  ),
                ),
            ],
          ),
        ],
      ),
    );

    if (rect == null) return card;

    // Position the card below the target if there's room, otherwise
    // above it — same "flip when it wouldn't fit" logic a Tooltip does,
    // hand-rolled here since positioning is relative to a raw Rect, not
    // Flutter's own Tooltip widget.
    final spaceBelow = screenSize.height - rect!.bottom;
    final showBelow = spaceBelow > 180;
    final top = showBelow ? rect!.bottom + 20 : null;
    final bottom = showBelow ? null : screenSize.height - rect!.top + 20;

    var left = rect!.center.dx - cardWidth / 2;
    left = left.clamp(margin, screenSize.width - cardWidth - margin);

    return Positioned(
      top: top,
      bottom: bottom,
      left: left,
      child: card,
    );
  }
}
