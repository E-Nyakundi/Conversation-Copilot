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
import 'tour/tour_controller.dart';
import 'tour/tour_help_button.dart';
import 'tour/tour_overlay.dart';
import 'tour/tour_step.dart';

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
///
/// Full-app hint system (2026-08-31, build plan §16): this is where the
/// mandatory guided tour actually runs, wrapping the whole shell in
/// TourOverlay — separate from OnboardingScreen (which only covers
/// welcome/mic-permission/knowledge-upload/ready, BEFORE this shell ever
/// mounts). Lewis's spec was "continuous onboarding... up to the very
/// end... even the settings page... all pages", which is why this lives
/// here rather than being folded into OnboardingScreen itself — the tour
/// needs to walk the REAL Home/History/Settings/LiveSession screens
/// after they exist, not a separate slideshow before them. All four
/// have real TourScreens: Home -> History -> Settings -> a pushed
/// LiveSessionScreen route (see _followTour/_startTourLiveSession below
/// for how a pushed route participates in what's otherwise an
/// IndexedStack tab sequence), then completion. On-demand replay
/// (TourHelpButton) is wired on Home/History/Settings; LiveSessionScreen
/// doesn't have one yet (see build plan §16 for why).
class AppShell extends StatefulWidget {
  const AppShell({super.key, required this.onSignOut});

  final Future<void> Function() onSignOut;

  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  int _tab = 0;

  // Full-app hint system (2026-08-31, build plan §16): keys held here
  // (State, not build()) so they're stable across rebuilds — see
  // home_screen.dart's doc comment on HomeScreen's key params for why a
  // key created inside build() wouldn't work for this.
  final _homeSettingsKey = GlobalKey();
  final _homeStartSessionKey = GlobalKey();
  final _homeViewAllKey = GlobalKey();
  final _historySearchKey = GlobalKey();
  final _historyFiltersKey = GlobalKey();
  final _settingsAnswerLengthKey = GlobalKey();
  final _settingsDiarizationKey = GlobalKey();
  final _settingsKnowledgeBaseKey = GlobalKey();
  final _settingsSignOutKey = GlobalKey();
  final _liveSessionMicKey = GlobalKey();

  late final TourController _tour;

  // Full-app hint system (2026-08-31, build plan §16): index each tab
  // screenId maps to in the IndexedStack below — kept in one place so
  // build()'s tab-following logic and the destinations list can't drift
  // out of sync with each other.
  static const _tabScreenIds = ['home', 'history', 'settings'];

  @override
  void initState() {
    super.initState();
    _tour = TourController(
      // 'live_session' deliberately included in screenOrder even though
      // it isn't one of AppShell's IndexedStack tabs — see build()'s
      // _followTour() and _startTourLiveSession() below for how a
      // PUSHED route participates in what's otherwise a tab-index-driven
      // sequence. This is the resolution to the build plan §16 checklist
      // item "LiveSessionScreen has no tour integration... wiring it in
      // will need slightly different mechanics".
      screenOrder: const ['home', 'history', 'settings', 'live_session'],
      screens: {
        'home': TourScreen(
          screenId: 'home',
          steps: [
            TourStep(
              id: 'home.settings',
              key: _homeSettingsKey,
              title: 'Settings, always one tap away',
              description:
                  'Manage your account, connectivity, and knowledge base from here — same screen as the Settings tab below.',
            ),
            TourStep(
              id: 'home.view_all',
              key: _homeViewAllKey,
              title: 'Your full session history',
              description: 'Every past session lives here, searchable, once you\'ve recorded a few.',
            ),
            TourStep(
              id: 'home.start_session',
              key: _homeStartSessionKey,
              title: 'This is where it starts',
              description:
                  'Tap the mic to start your first live session — Copilot listens and suggests answers as you talk. '
                  'We\'ll walk the rest of the app first, then you\'ll get here for real.',
            ),
          ],
        ),
        'history': TourScreen(
          screenId: 'history',
          steps: [
            TourStep(
              id: 'history.search',
              key: _historySearchKey,
              title: 'Search past transcripts',
              description: 'Find anything you or the other person said in a previous session.',
            ),
            TourStep(
              id: 'history.filters',
              key: _historyFiltersKey,
              title: 'Narrow it down',
              description: 'Filter to sessions that had suggestions, or just this week\'s.',
            ),
          ],
        ),
        'settings': TourScreen(
          screenId: 'settings',
          steps: [
            TourStep(
              id: 'settings.answer_length',
              key: _settingsAnswerLengthKey,
              title: 'How much detail you want',
              description: 'Quick, medium, or detailed — sets the default length for live suggestions.',
            ),
            TourStep(
              id: 'settings.diarization',
              key: _settingsDiarizationKey,
              title: 'Tell voices apart',
              description: 'Speaker diarization is more accurate but uses more battery — your call.',
            ),
            TourStep(
              id: 'settings.knowledge_base',
              key: _settingsKnowledgeBaseKey,
              title: 'Your knowledge base',
              description: 'Add or manage the CV, LinkedIn, or GitHub profile you uploaded earlier — anytime.',
            ),
            TourStep(
              id: 'settings.sign_out',
              key: _settingsSignOutKey,
              title: 'Signing out',
              description: 'You can always come back — you\'ll just need your email and password again.',
            ),
          ],
        ),
        'live_session': TourScreen(
          screenId: 'live_session',
          steps: [
            TourStep(
              id: 'live_session.mic',
              key: _liveSessionMicKey,
              title: 'Now, for real — start your session',
              description:
                  'Tap the mic to name this session, tell us what kind of questions to expect, and start listening.',
              // Lewis's spec: "component tips + a short task before
              // advancing" — the ONE task-gated step in the whole tour
              // (home.start_session above was deliberately changed to a
              // plain step — see its own comment — since the real "try
              // it" moment is here instead, on the actual live screen,
              // not a preview of it back on Home).
              requiresTask: true,
            ),
          ],
        ),
      },
    );
    _tour.load();
    _tour.addListener(_followTour);
  }

  @override
  void dispose() {
    _tour.removeListener(_followTour);
    _tour.dispose();
    super.dispose();
  }

  /// Full-app hint system (2026-08-31, build plan §16): keeps AppShell's
  /// visible tab in sync with the tour's current screen as it advances
  /// past Home into History/Settings, AND handles the one screen that
  /// isn't a tab at all (live_session) by actually pushing the route —
  /// resolving the build()-comment gap flagged when only Home was wired.
  bool _pushedTourLiveSession = false;

  void _followTour() {
    if (!_tour.isActive) return;
    final screenId = _tour.currentScreenId;
    if (screenId == null) return;

    if (screenId == 'live_session') {
      if (!_pushedTourLiveSession) {
        _pushedTourLiveSession = true;
        _startTourLiveSession();
      }
      return;
    }

    final tabIndex = _tabScreenIds.indexOf(screenId);
    if (tabIndex != -1 && tabIndex != _tab) {
      setState(() => _tab = tabIndex);
    }
  }

  /// Pushes LiveSessionScreen as part of the mandatory tour itself,
  /// passing the SAME TourController + mic GlobalKey down (see
  /// live_session_screen.dart's LiveSessionScreen.tour/micKey doc
  /// comment for why a pushed route needs explicit passing rather than
  /// reaching the controller through a shared ancestor the way the three
  /// IndexedStack tabs do). Deliberately does NOT reuse [_startSession]
  /// below — that path is for a real, user-initiated session start
  /// outside the tour and pops back to Home when the session ends; this
  /// one is a stop on the guided tour and should behave like one.
  Future<void> _startTourLiveSession() async {
    await Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => LiveSessionScreen(tour: _tour, micKey: _liveSessionMicKey),
      ),
    );
    // Whether the tour step's task was actually completed (advancing
    // TourController past 'live_session', see LiveSessionScreen's
    // _toggleMic hook) or the user backed out of this pushed route
    // without tapping the mic, _pushedTourLiveSession is reset here so a
    // later on-demand replay of the live_session step (this route has no
    // TourHelpButton of its own yet — see build plan §16) can push this
    // route again rather than being permanently latched from this one
    // pass. replayScreen('live_session') would still work if called from
    // elsewhere (e.g. a future help entry point on Home) since this
    // reset makes AppShell ready to push again either way.
    _pushedTourLiveSession = false;
  }

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
        settingsKey: _homeSettingsKey,
        startSessionKey: _homeStartSessionKey,
        viewAllKey: _homeViewAllKey,
        headerTrailing: TourHelpButton(tour: _tour, screenId: 'home'),
      ),
      // showBackButton: false — these are root tab destinations here,
      // not pushed routes; the mockups' back arrow is for when they're
      // reached via a pushed route elsewhere (e.g. Settings' own
      // "Connectivity test" row still pushes the Phase 0 ping harness
      // as a real route, which does want a back button).
      SessionHistoryScreen(
        showBackButton: false,
        searchKey: _historySearchKey,
        filtersKey: _historyFiltersKey,
        headerTrailing: TourHelpButton(tour: _tour, screenId: 'history'),
      ),
      SettingsScreen(
        showBackButton: false,
        onSignOut: widget.onSignOut,
        answerLengthKey: _settingsAnswerLengthKey,
        diarizationKey: _settingsDiarizationKey,
        knowledgeBaseKey: _settingsKnowledgeBaseKey,
        signOutKey: _settingsSignOutKey,
        headerTrailing: TourHelpButton(tour: _tour, screenId: 'settings'),
      ),
    ];

    // Full-app hint system (2026-08-31, build plan §16): _followTour
    // (registered as a _tour listener in initState) is what keeps _tab
    // in sync with the tour's progress and pushes LiveSessionScreen when
    // the tour reaches it — this build() doesn't need to do any of that
    // itself, just render whatever _tab currently is.
    return AnimatedBuilder(
      animation: _tour,
      builder: (context, _) {
        return TourOverlay(
          controller: _tour,
          child: Scaffold(
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
          ),
        );
      },
    );
  }
}

/// Phase 0 connectivity proof: mirrors server/latency_test.html's protocol
/// against PingConsumer (server/audio/consumers.py). Client sends
/// {"client_sent_at": `<ms epoch>`}, server echoes it with server-side
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