import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'auth/auth_repository.dart';
import 'auth/auth_store.dart';
import 'auth/change_password_screen.dart';
import 'auth/login_screen.dart';
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
/// 2026-08-24: "seen onboarding" is now persisted via shared_preferences
/// (the dependency flagged as follow-up work when this was in-memory-only)
/// instead of resetting on every cold start. `_ONBOARDED_KEY`'s value is
/// checked once in `initState` — a brief loading gap between app launch
/// and that read completing is unavoidable with an async storage API, so
/// `_loading` covers exactly that gap with an empty scaffold (no spinner —
/// the read is typically sub-millisecond on-device, and a flash of a
/// loading indicator before an already-fast check would be more visually
/// noisy than a blank frame). On any read failure (corrupted prefs,
/// platform channel error), defaults to `false` — treats it as a fresh
/// install rather than crashing or getting stuck loading, since
/// onboarding is a low-stakes one-time flow, not a security gate; worst
/// case a returning user sees onboarding once more.
class AppRoot extends StatefulWidget {
  const AppRoot({super.key});

  @override
  State<AppRoot> createState() => _AppRootState();
}

class _AppRootState extends State<AppRoot> {
  static const _onboardedKey = 'seen_onboarding';

  bool _onboarded = false;
  bool _loading = true;

  // Multi-tenancy retrofit (2026-08-30): auth is now checked before
  // onboarding, not instead of it — a returning, already-onboarded
  // tenant who's still logged in skips straight to AppShell; a signed-out
  // tenant (fresh install, or a token that stopped working — revoked,
  // expired, cleared) sees LoginScreen first regardless of whether
  // they've seen onboarding before. Onboarding and auth are independent
  // one-time gates, checked in that order because there's no point
  // showing product onboarding before establishing who's using the app.
  AuthUser? _user;

  @override
  void initState() {
    super.initState();
    _loadInitialState();
  }

  Future<void> _loadInitialState() async {
    bool onboarded = false;
    try {
      final prefs = await SharedPreferences.getInstance();
      onboarded = prefs.getBool(_onboardedKey) ?? false;
    } catch (_) {
      // Corrupted prefs / platform channel failure — fall back to
      // "not yet onboarded" rather than blocking app startup on this.
      onboarded = false;
    }

    // me() returns null for both "no saved token" and "token no longer
    // valid" — AppRoot treats those identically (show LoginScreen), see
    // AuthRepository.me()'s docstring for why that's the deliberate
    // choice here rather than trying to distinguish them.
    final authRepository = AuthRepository();
    final user = await authRepository.me();
    authRepository.dispose();

    if (!mounted) return;
    setState(() {
      _onboarded = onboarded;
      _user = user;
      _loading = false;
    });
  }

  Future<void> _markOnboarded() async {
    setState(() => _onboarded = true);
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setBool(_onboardedKey, true);
    } catch (_) {
      // Best-effort persistence: if this write fails, onboarding will
      // simply show again next cold start — not a crash-worthy failure.
    }
  }

  void _handleSignedIn(AuthUser user) {
    setState(() => _user = user);
  }

  Future<void> _handleSignedOut() async {
    await AuthStore.instance.clear();
    if (!mounted) return;
    setState(() => _user = null);
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const Scaffold(backgroundColor: CopilotColors.ink);
    }

    final user = _user;
    if (user == null) {
      return LoginScreen(onSignedIn: _handleSignedIn);
    }
    if (user.mustChangePassword) {
      return ChangePasswordScreen(onDone: _handleSignedIn);
    }
    if (!_onboarded) {
      return OnboardingScreen(onDone: _markOnboarded);
    }
    return AppShell(onSignOut: _handleSignedOut);
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
  const AppShell({super.key, required this.onSignOut});

  final Future<void> Function() onSignOut;

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
      SettingsScreen(showBackButton: false, onSignOut: widget.onSignOut),
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