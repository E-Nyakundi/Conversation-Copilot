import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'screens/home_screen.dart';
import 'screens/live_session_screen.dart';
import 'screens/onboarding_screen.dart';
import 'screens/session_history_screen.dart';
import 'screens/settings_screen.dart';
import 'theme.dart';

void main() {
  runApp(const CopilotApp());
}

class CopilotApp extends StatelessWidget {
  const CopilotApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Conversation Copilot',
      // Phase A (2026-08-23): release hygiene. This was never set, so
      // every build — including ones handed to Ed for real device
      // testing — showed Flutter's default red "DEBUG" ribbon in the
      // top-right corner regardless of build mode. Suppressing the
      // banner here doesn't change build mode/behavior at all, just the
      // visual artifact; `flutter run --release`/a real release build
      // is still what actually determines debug vs. release behavior.
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorSchemeSeed: CopilotColors.blue,
        useMaterial3: true,
        scaffoldBackgroundColor: CopilotColors.ink,
        brightness: Brightness.dark,
      ),
      home: const AppRoot(),
    );
  }
}

/// Gates the real app shell behind onboarding on first run.
///
/// 2026-08-14: this class — and the AppShell it wraps — didn't exist in
/// main.dart despite the build plan marking Phase 3.5's implementation
/// pass "[x] done" and session_history_screen.dart's own doc comment
/// already referring to "AppShell in main.dart" as an established thing.
/// The six screens (home/history/settings/onboarding/session
/// detail/live session) were all built expecting this shell to exist;
/// main.dart itself just never got the corresponding edit — apparently
/// dropped somewhere in merging parallel local/VM copies back together.
/// This restores it to match what the plan and the screens already
/// assumed was there.
///
/// Per the plan: "seen onboarding" is in-memory only for now (resets on
/// cold start) — a shared_preferences-backed flag is noted follow-up
/// work once that dependency is added to pubspec.yaml.
class AppRoot extends StatefulWidget {
  const AppRoot({super.key});

  @override
  State<AppRoot> createState() => _AppRootState();
}

class _AppRootState extends State<AppRoot> {
  bool _onboarded = false;

  @override
  Widget build(BuildContext context) {
    if (!_onboarded) {
      return OnboardingScreen(onDone: () => setState(() => _onboarded = true));
    }
    return const AppShell();
  }
}

/// Bottom-nav shell across Home / History / Settings.
///
/// Deliberate deviation from the onboarding/home mockups' gear-icon-only
/// Settings access: three root destinations map more naturally onto
/// Flutter's NavigationBar tab idiom than a single-screen-plus-gear
/// pattern. Home's own gear icon still jumps straight to the Settings
/// tab (via [_goToSettings]), so the one-tap access the mockup intended
/// is preserved even though the navigation model around it changed.
class AppShell extends StatefulWidget {
  const AppShell({super.key});

  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  int _tab = 0;

  void _goToSettings() => setState(() => _tab = 2);

  void _startSession() {
    Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => const LiveSessionScreen()),
    );
  }

  @override
  Widget build(BuildContext context) {
    final screens = [
      HomeScreen(
        onStartSession: _startSession,
        onOpenSettings: _goToSettings,
        onViewAllSessions: () => setState(() => _tab = 1),
      ),
      // showBackButton: false — these are root tab destinations here,
      // not pushed routes; the mockups' back arrow is for when they're
      // reached via a pushed route elsewhere (e.g. Settings' own
      // "Connectivity test" row still pushes the Phase 0 ping harness
      // as a real route, which does want a back button).
      const SessionHistoryScreen(showBackButton: false),
      const SettingsScreen(showBackButton: false),
    ];

    return Scaffold(
      backgroundColor: CopilotColors.ink,
      body: IndexedStack(index: _tab, children: screens),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab,
        onDestinationSelected: (i) => setState(() => _tab = i),
        backgroundColor: CopilotColors.panel,
        destinations: const [
          NavigationDestination(icon: Icon(Icons.home_outlined), selectedIcon: Icon(Icons.home), label: 'Home'),
          NavigationDestination(icon: Icon(Icons.history_outlined), selectedIcon: Icon(Icons.history), label: 'History'),
          NavigationDestination(icon: Icon(Icons.settings_outlined), selectedIcon: Icon(Icons.settings), label: 'Settings'),
        ],
      ),
    );
  }
}

/// Phase 0 connectivity proof: mirrors server/latency_test.html's protocol
/// against PingConsumer (server/audio/consumers.py). Client sends
/// {"client_sent_at": <ms epoch>}, server echoes it with server-side
/// timestamps added. Round trip is computed entirely on this device's own
/// clock (now - client_sent_at), so it's unaffected by any clock skew
/// between phone and server.
///
/// No longer the app's `home:` — reachable from Settings' "Connectivity
/// test" row (see settings_screen.dart) as a pushed route instead.
class PingTestScreen extends StatefulWidget {
  const PingTestScreen({super.key});

  @override
  State<PingTestScreen> createState() => _PingTestScreenState();
}

class _PingTestScreenState extends State<PingTestScreen> {
  final _urlController = TextEditingController(
    text: 'wss://145-241-102-99.sslip.io/ws/ping/',
  );
  final _log = <String>[];
  final _scrollController = ScrollController();

  WebSocketChannel? _channel;
  StreamSubscription? _subscription;
  bool _connected = false;
  String _status = 'Not connected.';

  void _appendLog(String line) {
    setState(() => _log.add(line));
    // Auto-scroll to the newest entry after the frame builds.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeOut,
        );
      }
    });
  }

  Future<void> _connect() async {
    final url = _urlController.text.trim();
    if (url.isEmpty) return;

    setState(() => _status = 'Connecting...');
    try {
      final channel = WebSocketChannel.connect(Uri.parse(url));
      // Wait for the underlying connection to actually establish before
      // declaring success — WebSocketChannel.connect() returns immediately,
      // it doesn't wait for the handshake.
      await channel.ready;

      _subscription = channel.stream.listen(
        _handlePong,
        onError: (error) {
          setState(() {
            _connected = false;
            _status = 'Error: $error';
          });
        },
        onDone: () {
          setState(() {
            _connected = false;
            _status = 'Disconnected.';
          });
        },
      );

      setState(() {
        _channel = channel;
        _connected = true;
        _status = 'Connected.';
      });
    } catch (e) {
      setState(() {
        _connected = false;
        _status = 'Connection failed: $e';
      });
    }
  }

  void _handlePong(dynamic message) {
    final now = DateTime.now().millisecondsSinceEpoch;
    try {
      final data = jsonDecode(message as String) as Map<String, dynamic>;
      final clientSentAt = (data['client_sent_at'] as num).toInt();
      final serverReceivedAt = (data['server_received_at'] as num).toDouble();
      final serverSentAt = (data['server_sent_at'] as num).toDouble();

      final roundTrip = now - clientSentAt;
      final up = serverReceivedAt - clientSentAt;
      final down = now - serverSentAt;

      _appendLog(
        'Round trip: ${roundTrip}ms  (up: ${up.round()}ms, down: ${down.round()}ms)',
      );
    } catch (e) {
      _appendLog('Failed to parse pong: $e (raw: $message)');
    }
  }

  void _sendPing() {
    if (_channel == null || !_connected) return;
    final clientSentAt = DateTime.now().millisecondsSinceEpoch;
    _channel!.sink.add(jsonEncode({'client_sent_at': clientSentAt}));
  }

  void _disconnect() {
    _subscription?.cancel();
    _channel?.sink.close();
    setState(() {
      _connected = false;
      _channel = null;
      _status = 'Disconnected.';
    });
  }

  @override
  void dispose() {
    _subscription?.cancel();
    _channel?.sink.close();
    _urlController.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('WebSocket Ping Test (Phase 0)'),
        actions: [
          IconButton(
            icon: const Icon(Icons.mic),
            tooltip: 'Live Session (Phase 1)',
            onPressed: () {
              Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const LiveSessionScreen()),
              );
            },
          ),
        ],
      ),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              'Direct wss:// connectivity proof — no tunnel, talks straight '
              'to the VM. Test over cellular data for a realistic reading.',
              style: TextStyle(color: Colors.grey),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _urlController,
              enabled: !_connected,
              decoration: const InputDecoration(
                border: OutlineInputBorder(),
                labelText: 'WSS URL',
              ),
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                ElevatedButton(
                  onPressed: _connected ? null : _connect,
                  child: const Text('Connect'),
                ),
                const SizedBox(width: 8),
                ElevatedButton(
                  onPressed: _connected ? _sendPing : null,
                  child: const Text('Send Ping'),
                ),
                const SizedBox(width: 8),
                ElevatedButton(
                  onPressed: _connected ? _disconnect : null,
                  child: const Text('Disconnect'),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Text(_status, style: const TextStyle(fontWeight: FontWeight.bold)),
            const SizedBox(height: 12),
            Expanded(
              child: Container(
                decoration: BoxDecoration(
                  border: Border.all(color: Colors.grey.shade300),
                  borderRadius: BorderRadius.circular(8),
                ),
                padding: const EdgeInsets.all(8),
                child: ListView.builder(
                  controller: _scrollController,
                  itemCount: _log.length,
                  itemBuilder: (context, index) => Text(
                    _log[index],
                    style: const TextStyle(fontFamily: 'monospace', fontSize: 13),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}