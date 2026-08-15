// This is a basic Flutter widget test.
//
// To perform an interaction with a widget in your test, use the WidgetTester
// utility in the flutter_test package. For example, you can send tap and scroll
// gestures. You can also use WidgetTester to find child widgets in the widget
// tree, read text, and verify that the values of widget properties are correct.

// Basic smoke test — replaces the stock `flutter create` counter-demo test
// (2026-08-14), which referenced a `MyApp` class that never existed in this
// project; it was leftover scaffolding from before `CopilotApp` (main.dart)
// became the real root widget, and `flutter analyze` flagged it as a
// compile error (`creation_with_non_type`) once actually checked.
//
// This just verifies the app builds and renders its root screen without
// throwing — not a real interaction test. Expand per-screen as needed.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:flutter_app/main.dart';

void main() {
  testWidgets('CopilotApp builds without throwing', (WidgetTester tester) async {
    await tester.pumpWidget(const CopilotApp());
    await tester.pump();

    // Root widget mounted successfully — that's the whole assertion for
    // this smoke test. Deliberately not asserting on specific text/state
    // since the app connects to a live backend and starts on whatever
    // screen main.dart wires up first.
    expect(find.byType(MaterialApp), findsOneWidget);
  });
}
