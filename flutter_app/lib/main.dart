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

/// Root widget. Onboarding is shown once per app launch and gates the main
/// app shell — there's no persistence layer yet (see build plan), so this
/// is in-memory only and will show again on the next cold start. That's an
/// acceptable gap for now; wiring a "seen onboarding" flag needs a local
/// storage package that isn't in pubspec.yaml yet (e.g. shared_preferences).
class CopilotApp extends StatefulWidget {
  const CopilotApp({super.key});

  @override
  State<CopilotApp> createState() => _CopilotAppState();
}

class _CopilotAppState extends State<CopilotApp> {
  bool _onboarded = false;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Conversation Copilot',
      debugShowCheckedModeBanner: false,
      theme: buildCopilotTheme(),
      home: _onboarded
          ? const AppShell()
          : OnboardingScreen(onDone: () => setState(() => _onboarded = true)),
    );
  }
}

/// Main app shell: bottom nav across Home / History / Settings. Live
/// Session is reached via Home's Start Session button (or Session
/// Detail/History rows push their own routes) rather than living in the
/// tab bar itself, since it's a full-screen, focused, in-progress-call
/// experience rather than something you'd casually tab back and forth
/// into mid-session.
class AppShell extends StatefulWidget {
  const AppShell({super.key});

  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  int _index = 0;

  void _goToTab(int i) => setState(() => _index = i);

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
        onOpenSettings: () => _goToTab(2),
        onViewAllSessions: () => _goToTab(1),
      ),
      const SessionHistoryScreen(showBackButton: false),
      const SettingsScreen(showBackButton: false),
    ];

    return Scaffold(
      backgroundColor: CopilotColors.ink,
      body: SafeArea(
        bottom: false,
        child: IndexedStack(index: _index, children: screens),
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: _goToTab,
        destinations: const [
          NavigationDestination(icon: Icon(Icons.home_outlined), label: 'Home'),
          NavigationDestination(icon: Icon(Icons.history), label: 'History'),
          NavigationDestination(icon: Icon(Icons.settings_outlined), label: 'Settings'),
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
/// This was the very first Phase 0/1 test harness and predates the
/// designed app — it's no longer part of primary navigation (see
/// Phase 3.5), but stays reachable from Settings > Advanced >
/// "Connectivity test" since it's still a useful raw debug tool.
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
            tooltip: 'Live Session',
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
