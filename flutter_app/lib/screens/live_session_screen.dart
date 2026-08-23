import 'dart:async';

import 'package:flutter/material.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import '../audio/capture.dart';
import '../theme.dart';
import '../widgets/paced_code_reveal.dart';
import '../ws/socket.dart';

/// Phase 1: the Flutter equivalent of server/audio_test.html — mic capture
/// -> WebSocket -> live transcript, with Phase 3 suggestion bubbles layered
/// in. Talks to AudioStreamConsumer at ws/audio/ (or ws/audio/<uuid>/ to
/// resume a session).
///
/// Phase 3.5/implementation: restyled to the instrument-panel visual
/// identity (design/mockups/live_session_mockup.html) — telemetry strip
/// (status dot + level toggle) up top, transcript + suggestion cards in
/// the middle, amplitude-ring mic control at the bottom. All WS/mic
/// logic below is unchanged from the working test-harness version; only
/// the build() method and a couple of small additions (connect sheet,
/// ring pulse animation) are new.
class LiveSessionScreen extends StatefulWidget {
  const LiveSessionScreen({super.key});

  @override
  State<LiveSessionScreen> createState() => _LiveSessionScreenState();
}

enum _EntryKind { finalTurn, suggestion }

/// Phase 4: Quick / Medium / Detailed answer-level switching. Values
/// match the server's ai/prompts.py:ANSWER_LEVELS keys exactly — sent
/// verbatim in the {"type": "set_answer_level", "level": ...} message.
enum AnswerLevel { quick, medium, detailed }

extension on AnswerLevel {
  String get wireValue => name; // 'quick' / 'medium' / 'detailed'
  String get label => switch (this) {
        AnswerLevel.quick => 'Quick',
        AnswerLevel.medium => 'Medium',
        AnswerLevel.detailed => 'Detailed',
      };
}

class _TranscriptEntry {
  _TranscriptEntry.finalTurn({
    required this.speaker,
    required this.text,
    required this.confidence,
  })  : kind = _EntryKind.finalTurn,
        latencyMs = null,
        answerLevel = null,
        extractionConfidence = null,
        answerFormat = null,
        secondaryAnswer = null;

  _TranscriptEntry.suggestion({
    required this.text,
    required this.latencyMs,
    required this.answerLevel,
    required this.extractionConfidence,
    this.answerFormat,
    this.secondaryAnswer,
  })  : kind = _EntryKind.suggestion,
        speaker = null,
        confidence = null;

  final _EntryKind kind;
  final String? speaker;
  final String text;
  final double? confidence; // final-turn (STT) confidence
  final int? latencyMs;
  final String? answerLevel;
  // Extraction confidence, not model confidence — see the matching note
  // in server/audio/consumers.py._generate_suggestion. There's no
  // reliable LLM self-confidence signal yet (deferred per the build
  // plan), so this reflects how confident the question-extractor was
  // that this text was actually a question worth answering.
  final double? extractionConfidence;

  // Phase 5 (2026-08-16): 'prose' (default/omitted), 'code', or
  // 'code_and_prose' — see server/ai/answer_format.py. When 'code' or
  // 'code_and_prose', `text` holds the code answer (backend contract:
  // answer_text is always the code half when both were generated —
  // see conversation/models.py:Suggestion). secondaryAnswer is only
  // ever non-null for 'code_and_prose', holding the companion spoken
  // answer. No real code-block rendering yet (monospace + a plain
  // scrollable text block only) — proper syntax highlighting is
  // deliberately deferred; see the build plan entry for this feature.
  final String? answerFormat;
  final String? secondaryAnswer;
}

class _LiveSessionScreenState extends State<LiveSessionScreen>
    with SingleTickerProviderStateMixin {
  final _urlController = TextEditingController(
    text: 'wss://145-241-102-99.sslip.io/ws/audio/',
  );
  final _socket = CopilotSocket();
  final _mic = MicCapture();
  final _entries = <_TranscriptEntry>[];
  final _scrollController = ScrollController();
  // Full, continuous transcript — every finalized turn appended in order.
  // Distinct from `_entries` above: `_entries` is the existing
  // per-turn/per-suggestion box (each final turn its own bubble, badges,
  // suggestion cards interleaved); this is the plain, unbroken "everything
  // that's been said" view the transcript box shows.
  final List<String> _fullTranscriptLines = [];
  final _fullTranscriptScrollController = ScrollController();

  // Phase 4 (2026-08-16, build plan §11b): smart paced code-reveal
  // orchestration. Every code-format suggestion gets its own controller
  // (created lazily via _controllerFor) so its card always renders
  // through PacedCodeReveal; `_activeCodeEntry` tracks whichever one is
  // the current "being copied right now" card for queueing decisions —
  // the most recently shown code card that hasn't finished or been
  // manually paused yet.
  //
  // Renamed from _pendingCodeSuggestions (2026-08-23): the interrupting
  // text overlay this queue used to sit alongside was removed the same
  // day (see _addSuggestionEntry) — it was built on a mistaken read of
  // a VAD-chunking bug that Phase C's completion_heuristic.py (§14) now
  // fixes at the source, so what was left was mostly genuinely separate
  // questions where interrupting a code card mid-copy was just noise.
  // Text suggestions now queue exactly like code ones always did, so
  // this single list (and its "N new suggestions" flush UI) covers both.
  final Map<_TranscriptEntry, PacedCodeRevealController> _codeControllers = {};
  _TranscriptEntry? _activeCodeEntry;
  final List<_TranscriptEntry> _pendingSuggestions = [];

  StreamSubscription? _msgSub;
  StreamSubscription? _errSub;
  StreamSubscription? _doneSub;

  String? _sessionId;
  bool _connected = false;
  bool _recording = false;
  String _status = 'Not connected.';

  // Phase 4: defaults to Medium, matching ai/prompts.py's
  // DEFAULT_ANSWER_LEVEL — stays in sync with the server without needing
  // an explicit message until the user actually changes it.
  AnswerLevel _answerLevel = AnswerLevel.medium;

  // Drives the amplitude-ring pulse while recording. There's no real
  // per-sample amplitude signal exposed by MicCapture (see capture.dart) —
  // this is a steady pulse to communicate "listening", not a true VAD
  // level meter. Swap for a real level stream if MicCapture ever exposes
  // one.
  late final AnimationController _ringController = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 900),
  )..repeat(reverse: true);

  void _setAnswerLevel(AnswerLevel level) {
    setState(() => _answerLevel = level);
    if (_connected) {
      _socket.send({'type': 'set_answer_level', 'level': level.wireValue});
    }
  }

  @override
  void initState() {
    super.initState();
    // Phase 4 (2026-08-16, build plan §11b): keep the screen awake for the
    // WHOLE live session, not scoped to only while a code card is
    // actively being paced — decided explicitly during design (one state
    // to manage, simpler than tracking "engaged" moments). Neither
    // Android (WAKE_LOCK) nor iOS (isIdleTimerDisabled) needs a runtime
    // permission prompt for this. Known limitation, not handled specially
    // here: the OS releases this automatically the moment the app
    // backgrounds (incoming call, app switch) — this is a "stay awake
    // while in the foreground" feature, not a background-survival one.
    WakelockPlus.enable();
  }

  PacedCodeRevealController _controllerFor(_TranscriptEntry entry) {
    return _codeControllers.putIfAbsent(entry, () => PacedCodeRevealController());
  }

  bool get _activeCodeStillPacing {
    final entry = _activeCodeEntry;
    if (entry == null) return false;
    final controller = _codeControllers[entry];
    return controller != null && !controller.isFinished && !controller.manuallyOverridden;
  }

  /// Design change (2026-08-23): the interrupting-overlay behavior for
  /// text suggestions is REMOVED. It was originally built (§11b) on a
  /// mistaken read of a VAD-chunking bug — a single continuous
  /// interviewer utterance was sometimes finalized as two separate
  /// turns, which looked like "a second question arrived while
  /// answering the first" and the overlay was meant to let the person
  /// quickly clear the (apparent) second question and get back to the
  /// first. Phase C's completion_heuristic.py (§14) fixes that
  /// fragmentation at the source, so the scenario the overlay existed
  /// for is now rare — what's left is mostly genuinely separate,
  /// rapid-fire questions, where interrupting the code card the person
  /// is actively hand-typing from is just noise, not help (confirmed
  /// on-device: it was firing mid-copy on an unrelated question).
  /// Text suggestions now behave exactly like code ones already did:
  /// if a code card is actively being paced, hold the new suggestion
  /// silently and surface it the same way as any other queued item —
  /// no pause, no paint-on-top-of-what's-being-copied.
  void _addSuggestionEntry(_TranscriptEntry entry) {
    final isCodeEntry = entry.answerFormat == 'code' || entry.answerFormat == 'code_and_prose';

    if (_activeCodeStillPacing) {
      _pendingSuggestions.add(entry);
      return;
    }

    _entries.add(entry);
    if (isCodeEntry) {
      _controllerFor(entry);
      _activeCodeEntry = entry;
    }
  }

  /// The one deliberate touch exception on the "pending" side, same as
  /// the paced-reveal widget's own "tap to resume" affordance — a single
  /// tap on the "N new suggestion(s)" chip, not continuous interaction.
  void _flushPendingSuggestions() {
    setState(() {
      for (final entry in _pendingSuggestions) {
        _entries.add(entry);
        // Design change (2026-08-23): this list can now hold text
        // entries too (see _addSuggestionEntry above), so only code
        // entries get a PacedCodeRevealController / become the new
        // "active" card — a queued text suggestion just renders
        // normally, same as it always did outside a pacing session.
        final isCodeEntry = entry.answerFormat == 'code' || entry.answerFormat == 'code_and_prose';
        if (isCodeEntry) {
          _controllerFor(entry);
          _activeCodeEntry = entry;
        }
      }
      _pendingSuggestions.clear();
    });
    _scrollToEnd();
  }

  void _scrollToEnd() {
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

  void _scrollFullTranscriptToEnd() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_fullTranscriptScrollController.hasClients) {
        _fullTranscriptScrollController.animateTo(
          _fullTranscriptScrollController.position.maxScrollExtent,
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
      await _socket.connect(url);
      _msgSub = _socket.messages.listen(_handleMessage);
      _errSub =
          _socket.errors.listen((e) => setState(() => _status = 'Error: $e'));
      _doneSub = _socket.onDone.listen((_) {
        setState(() {
          _connected = false;
          _status = 'Disconnected.';
        });
      });
      setState(() {
        _connected = true;
        _status = 'Connected. Waiting for session_started...';
      });
    } catch (e) {
      setState(() {
        _connected = false;
        _status = 'Connection failed: $e';
      });
    }
  }

  void _handleMessage(Map<String, dynamic> data) {
    switch (data['type']) {
      case 'session_started':
        setState(() {
          _sessionId = data['session_id'] as String?;
          _status = 'Session: $_sessionId';
          // A fresh WS connection means a fresh (or resumed-empty, from the
          // server's point of view) transcript stream — don't carry stale
          // text over from a previous connect/disconnect cycle in this
          // widget's lifetime.
          _fullTranscriptLines.clear();
        });
        // Phase 4: server defaults to Medium too, but this covers the
        // case where the user picked a different level before hitting
        // Connect (the button is enabled regardless of connection state).
        _socket.send({'type': 'set_answer_level', 'level': _answerLevel.wireValue});
        break;
      case 'answer_level_set':
        // Server's ack of set_answer_level — mostly informational, but
        // keeps the UI honest if a message ever gets normalized
        // server-side (see ai.prompts.normalize_level) to something
        // other than what was requested.
        final level = data['level'] as String?;
        final matched = AnswerLevel.values.where((l) => l.wireValue == level);
        if (matched.isNotEmpty) {
          setState(() => _answerLevel = matched.first);
        }
        break;
      case 'final':
        setState(() {
          final speaker = data['speaker'] as String? ?? '?';
          final text = data['text'] as String? ?? '';
          _entries.add(_TranscriptEntry.finalTurn(
            speaker: speaker,
            text: text,
            confidence: (data['confidence'] as num?)?.toDouble(),
          ));
          // Full transcript gets the raw finalized text, unfiltered and
          // un-badged — this box is meant to be "everything said", not a
          // curated/extracted view. Speaker label kept as a light prefix
          // since it costs nothing and helps readability, but nothing is
          // trimmed, scored, or left out.
          _fullTranscriptLines.add('$speaker: $text');
        });
        _scrollToEnd();
        _scrollFullTranscriptToEnd();
        break;
      case 'suggestion':
        // Phase 3: arrives asynchronously, some time after the `final`
        // message for the turn that triggered it (local LLM / Groq call
        // runs as a background task server-side) — just append whenever
        // it shows up, same as audio_test.html.
        //
        // Phase 4 (2026-08-16, §11b): routed through _addSuggestionEntry
        // rather than appended directly — see that method for the
        // queue-vs-overlay decision when a code card is already actively
        // being copied.
        setState(() {
          _addSuggestionEntry(_TranscriptEntry.suggestion(
            text: data['answer'] as String? ?? '',
            latencyMs: (data['latency_ms'] as num?)?.toInt(),
            answerLevel: data['answer_level'] as String?,
            extractionConfidence: (data['extraction_confidence'] as num?)?.toDouble(),
            answerFormat: data['answer_format'] as String?,
            secondaryAnswer: data['secondary_answer'] as String?,
          ));
        });
        _scrollToEnd();
        break;
      case 'session_ended':
        setState(() => _status = 'Session $_sessionId ended.');
        break;
      // 'extraction' messages also arrive (Phase 2) but aren't rendered
      // here yet, matching audio_test.html — revisit once there's a UI
      // spot for question/decision/objection/etc. badges.
    }
  }

  Future<void> _startMic() async {
    try {
      await _mic.start((chunk) => _socket.sendAudio(chunk));
      setState(() {
        _recording = true;
        _status = 'Streaming mic @ ${MicCapture.sampleRate}Hz.';
      });
    } catch (e) {
      setState(() => _status = 'Mic error: $e');
    }
  }

  Future<void> _stopMic() async {
    await _mic.stop();
    setState(() {
      _recording = false;
      _status = 'Mic stopped.';
    });
  }

  void _endSession() {
    _socket.sendEndSession();
  }

  Future<void> _disconnect() async {
    if (_recording) await _mic.stop();
    await _socket.disconnect();
    setState(() {
      _connected = false;
      _recording = false;
      _sessionId = null;
      _status = 'Disconnected.';
    });
  }

  void _toggleMic() {
    if (!_connected) {
      _showConnectSheet();
      return;
    }
    _recording ? _stopMic() : _startMic();
  }

  void _showConnectSheet() {
    showModalBottomSheet(
      context: context,
      backgroundColor: CopilotColors.panel,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(18)),
      ),
      builder: (sheetContext) {
        return Padding(
          padding: EdgeInsets.fromLTRB(
              20, 20, 20, 20 + MediaQuery.of(sheetContext).viewInsets.bottom),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Text(
                'Connect to server',
                style: TextStyle(
                  fontFamily: CopilotFonts.display,
                  fontWeight: FontWeight.w600,
                  fontSize: 16,
                  color: CopilotColors.paper,
                ),
              ),
              const SizedBox(height: 14),
              TextField(
                controller: _urlController,
                style: const TextStyle(color: CopilotColors.paper, fontSize: 13),
                decoration: InputDecoration(
                  filled: true,
                  fillColor: CopilotColors.panelRaised,
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(10),
                    borderSide: BorderSide.none,
                  ),
                  labelText: 'wss://.../ws/audio/',
                  labelStyle: const TextStyle(color: CopilotColors.mist),
                ),
              ),
              const SizedBox(height: 14),
              ElevatedButton(
                onPressed: () async {
                  await _connect();
                  if (sheetContext.mounted) Navigator.of(sheetContext).pop();
                },
                child: const Text('Connect'),
              ),
            ],
          ),
        );
      },
    );
  }

  @override
  void dispose() {
    WakelockPlus.disable();
    for (final controller in _codeControllers.values) {
      controller.dispose();
    }
    _msgSub?.cancel();
    _errSub?.cancel();
    _doneSub?.cancel();
    _mic.dispose();
    _socket.dispose();
    _urlController.dispose();
    _scrollController.dispose();
    _fullTranscriptScrollController.dispose();
    _ringController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: CopilotColors.ink,
      body: SafeArea(
        child: Column(
          children: [
            _TelemetryStrip(
              connected: _connected,
              recording: _recording,
              status: _status,
              answerLevel: _answerLevel,
              onLevelChanged: _setAnswerLevel,
            ),
            Expanded(
              child: DefaultTabController(
                length: 2,
                child: Column(
                  children: [
                    const TabBar(
                      labelColor: CopilotColors.paper,
                      unselectedLabelColor: CopilotColors.mist,
                      indicatorColor: CopilotColors.amber,
                      labelStyle: TextStyle(
                          fontFamily: CopilotFonts.display,
                          fontWeight: FontWeight.w500,
                          fontSize: 12.5),
                      tabs: [
                        Tab(text: 'Transcript'),
                        Tab(text: 'Suggestions'),
                      ],
                    ),
                    Expanded(
                      child: TabBarView(
                        children: [
                          _FullTranscriptView(
                            lines: _fullTranscriptLines,
                            controller: _fullTranscriptScrollController,
                          ),
                          _EntriesView(
                            entries: _entries,
                            controller: _scrollController,
                            controllerFor: _controllerFor,
                            activeCodeEntry: _activeCodeEntry,
                            pendingCount: _pendingSuggestions.length,
                            onFlushPending: _flushPendingSuggestions,
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ),
            _Controls(
              connected: _connected,
              recording: _recording,
              ringController: _ringController,
              onMicTap: _toggleMic,
              onEndSession: _connected ? _endSession : null,
              onDisconnect: _connected ? () => _disconnect() : null,
            ),
          ],
        ),
      ),
    );
  }
}

class _TelemetryStrip extends StatelessWidget {
  const _TelemetryStrip({
    required this.connected,
    required this.recording,
    required this.status,
    required this.answerLevel,
    required this.onLevelChanged,
  });

  final bool connected;
  final bool recording;
  final String status;
  final AnswerLevel answerLevel;
  final ValueChanged<AnswerLevel> onLevelChanged;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(20, 10, 20, 14),
      decoration: const BoxDecoration(
        border: Border(bottom: BorderSide(color: CopilotColors.hairline)),
      ),
      child: Row(
        children: [
          Container(
            width: 8,
            height: 8,
            decoration: BoxDecoration(
              color: recording ? CopilotColors.amber : CopilotColors.hairline,
              shape: BoxShape.circle,
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              status,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                fontFamily: CopilotFonts.mono,
                fontSize: 12.5,
                color: CopilotColors.paper,
              ),
            ),
          ),
          const SizedBox(width: 8),
          Container(
            decoration: BoxDecoration(
              color: CopilotColors.panel,
              borderRadius: BorderRadius.circular(8),
            ),
            padding: const EdgeInsets.all(2),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                for (final level in AnswerLevel.values)
                  GestureDetector(
                    onTap: () => onLevelChanged(level),
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 5),
                      decoration: BoxDecoration(
                        color: level == answerLevel ? CopilotColors.panelRaised : null,
                        borderRadius: BorderRadius.circular(6),
                      ),
                      child: Text(
                        level.name,
                        style: TextStyle(
                          fontFamily: CopilotFonts.mono,
                          fontSize: 10,
                          color: level == answerLevel
                              ? CopilotColors.amber
                              : CopilotColors.mist,
                        ),
                      ),
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _FullTranscriptView extends StatelessWidget {
  const _FullTranscriptView({required this.lines, required this.controller});

  final List<String> lines;
  final ScrollController controller;

  @override
  Widget build(BuildContext context) {
    if (lines.isEmpty) {
      return const _EmptyPane(text: 'Nothing transcribed yet.\nStart the mic to begin.');
    }
    return SingleChildScrollView(
      controller: controller,
      padding: const EdgeInsets.all(20),
      child: Text(
        lines.join('\n\n'),
        style: const TextStyle(
          fontSize: 13.5,
          height: 1.55,
          color: CopilotColors.paper,
        ),
      ),
    );
  }
}

class _EntriesView extends StatelessWidget {
  const _EntriesView({
    required this.entries,
    required this.controller,
    required this.controllerFor,
    required this.activeCodeEntry,
    required this.pendingCount,
    required this.onFlushPending,
  });

  final List<_TranscriptEntry> entries;
  final ScrollController controller;
  // Phase 4 §11b: this widget is stateless and owns none of the paced-
  // reveal state itself — all of it lives in _LiveSessionScreenState (so
  // it persists correctly across rebuilds and can be reasoned about
  // alongside the WebSocket/session lifecycle) and is threaded through
  // here as plain callbacks/values.
  final PacedCodeRevealController Function(_TranscriptEntry) controllerFor;
  final _TranscriptEntry? activeCodeEntry;
  final int pendingCount;
  final VoidCallback onFlushPending;

  @override
  Widget build(BuildContext context) {
    if (entries.isEmpty) {
      return const _EmptyPane(
          text: 'Questions and suggested answers\nwill appear here as they\'re detected.');
    }
    return Column(
      children: [
        Expanded(
          child: ListView.builder(
      controller: controller,
      padding: const EdgeInsets.fromLTRB(20, 18, 20, 20),
      itemCount: entries.length,
      itemBuilder: (context, index) {
        final entry = entries[index];
        if (entry.kind == _EntryKind.finalTurn) {
          final conf = entry.confidence != null
              ? '${(entry.confidence! * 100).round()}%'
              : null;
          return Padding(
            padding: const EdgeInsets.only(bottom: 16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  (entry.speaker ?? '?').toUpperCase(),
                  style: const TextStyle(
                    fontFamily: CopilotFonts.mono,
                    fontSize: 10,
                    letterSpacing: 1,
                    color: CopilotColors.mist,
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  entry.text,
                  style: const TextStyle(
                      fontSize: 14.5, height: 1.55, color: CopilotColors.paper),
                ),
                if (conf != null) ...[
                  const SizedBox(height: 3),
                  Text('confidence $conf',
                      style: const TextStyle(
                          fontFamily: CopilotFonts.mono,
                          fontSize: 10,
                          color: CopilotColors.mist)),
                ],
              ],
            ),
          );
        }

        final levelLabel = entry.answerLevel;
        final confLabel = entry.extractionConfidence != null
            ? 'conf ${(entry.extractionConfidence! * 100).round()}%'
            : null;
        final isCode = entry.answerFormat == 'code' || entry.answerFormat == 'code_and_prose';
        final cardLabel = entry.answerFormat == 'code'
            ? 'Suggested code'
            : entry.answerFormat == 'code_and_prose'
                ? 'Suggested code (+ spoken alternative below)'
                : 'Suggested answer';
        return Container(
          margin: const EdgeInsets.only(bottom: 16),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
          decoration: const BoxDecoration(
            color: CopilotColors.panel,
            border: Border(left: BorderSide(color: CopilotColors.blue, width: 3)),
            borderRadius: BorderRadius.horizontal(right: Radius.circular(10)),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Icon(
                    isCode ? Icons.code : Icons.lightbulb_outline,
                    size: 13,
                    color: CopilotColors.blue,
                  ),
                  const SizedBox(width: 6),
                  Text(
                    cardLabel,
                    style: const TextStyle(
                      fontFamily: CopilotFonts.display,
                      fontWeight: FontWeight.w500,
                      fontSize: 12,
                      color: CopilotColors.blue,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 6),
              // Code answers rendered through the Phase 4 paced reveal
              // (build plan §11b) — stepped, one line at a time, paced by
              // that line's own complexity, so the user can hand-copy it
              // to another device without needing to touch this screen.
              // Prose answers stay as plain SelectableText, unchanged.
              if (isCode)
                PacedCodeReveal(
                  code: entry.text,
                  controller: controllerFor(entry),
                )
              else
                SelectableText(
                  entry.text,
                  style: const TextStyle(
                    fontSize: 13.5,
                    height: 1.5,
                    color: CopilotColors.paper,
                  ),
                ),
              if (entry.secondaryAnswer != null) ...[
                const SizedBox(height: 12),
                const Divider(height: 1, color: CopilotColors.mist),
                const SizedBox(height: 10),
                const Text(
                  'ALTERNATIVE (SPOKEN)',
                  style: TextStyle(
                    fontFamily: CopilotFonts.mono,
                    fontSize: 10,
                    letterSpacing: 0.5,
                    color: CopilotColors.mist,
                  ),
                ),
                const SizedBox(height: 6),
                Text(
                  entry.secondaryAnswer!,
                  style: const TextStyle(fontSize: 13.5, height: 1.5, color: CopilotColors.paper),
                ),
              ],
              const SizedBox(height: 8),
              Row(
                children: [
                  if (levelLabel != null)
                    Text(levelLabel,
                        style: const TextStyle(
                            fontFamily: CopilotFonts.mono,
                            fontSize: 10,
                            color: CopilotColors.mist)),
                  if (entry.latencyMs != null) ...[
                    const SizedBox(width: 12),
                    Text('${entry.latencyMs}ms',
                        style: const TextStyle(
                            fontFamily: CopilotFonts.mono,
                            fontSize: 10,
                            color: CopilotColors.mist)),
                  ],
                  if (confLabel != null) ...[
                    const SizedBox(width: 12),
                    Text(confLabel,
                        style: const TextStyle(
                            fontFamily: CopilotFonts.mono,
                            fontSize: 10,
                            color: CopilotColors.mist)),
                  ],
                ],
              ),
            ],
          ),
        );
      },
          ),
        ),
        if (pendingCount > 0)
          GestureDetector(
            onTap: onFlushPending,
            child: Container(
              width: double.infinity,
              margin: const EdgeInsets.fromLTRB(20, 0, 20, 14),
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              decoration: BoxDecoration(
                color: CopilotColors.panel,
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: CopilotColors.blue, width: 1),
              ),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  const Icon(Icons.notifications_active_outlined,
                      size: 14, color: CopilotColors.blue),
                  const SizedBox(width: 8),
                  Text(
                    pendingCount == 1
                        ? '1 new suggestion ready — tap to show'
                        : '$pendingCount new suggestions ready — tap to show',
                    style: const TextStyle(
                      fontFamily: CopilotFonts.display,
                      fontSize: 12,
                      color: CopilotColors.blue,
                    ),
                  ),
                ],
              ),
            ),
          ),
      ],
    );
  }
}

class _EmptyPane extends StatelessWidget {
  const _EmptyPane({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 40),
        child: Text(
          text,
          textAlign: TextAlign.center,
          style: const TextStyle(color: CopilotColors.mist, fontSize: 12.5, height: 1.5),
        ),
      ),
    );
  }
}

class _Controls extends StatelessWidget {
  const _Controls({
    required this.connected,
    required this.recording,
    required this.ringController,
    required this.onMicTap,
    required this.onEndSession,
    required this.onDisconnect,
  });

  final bool connected;
  final bool recording;
  final AnimationController ringController;
  final VoidCallback onMicTap;
  final VoidCallback? onEndSession;
  final VoidCallback? onDisconnect;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 20),
      decoration: const BoxDecoration(
        border: Border(top: BorderSide(color: CopilotColors.hairline)),
      ),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          _SideButton(
            icon: Icons.stop_circle_outlined,
            onTap: onEndSession,
            enabled: onEndSession != null,
          ),
          const SizedBox(width: 28),
          GestureDetector(
            onTap: onMicTap,
            child: SizedBox(
              width: 84,
              height: 84,
              child: AnimatedBuilder(
                animation: ringController,
                builder: (context, child) {
                  return CustomPaint(
                    painter: _RingPainter(
                      pulse: recording ? ringController.value : 0,
                      active: recording,
                    ),
                    child: child,
                  );
                },
                child: Center(
                  child: Container(
                    width: 60,
                    height: 60,
                    decoration: const BoxDecoration(
                      color: CopilotColors.panelRaised,
                      shape: BoxShape.circle,
                    ),
                    child: Icon(
                      recording ? Icons.mic : Icons.mic_none,
                      color: CopilotColors.amber,
                      size: 24,
                    ),
                  ),
                ),
              ),
            ),
          ),
          const SizedBox(width: 28),
          _SideButton(
            icon: Icons.close,
            onTap: onDisconnect,
            enabled: onDisconnect != null,
          ),
        ],
      ),
    );
  }
}

class _SideButton extends StatelessWidget {
  const _SideButton({required this.icon, required this.onTap, required this.enabled});

  final IconData icon;
  final VoidCallback? onTap;
  final bool enabled;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: enabled ? onTap : null,
      child: Container(
        width: 44,
        height: 44,
        decoration: const BoxDecoration(
          color: CopilotColors.panel,
          shape: BoxShape.circle,
        ),
        child: Icon(
          icon,
          size: 18,
          color: enabled ? CopilotColors.mist : CopilotColors.hairline,
        ),
      ),
    );
  }
}

/// Segmented ring matching the mockup's `.ring-tick` motif. `pulse` is a
/// 0..1 animation value; `active` switches between the static
/// mostly-dim look (idle) and a pulse where a rotating band of ticks
/// lights up amber (recording) — see the docstring on `_ringController`
/// for why this isn't driven by real audio amplitude yet.
class _RingPainter extends CustomPainter {
  _RingPainter({required this.pulse, required this.active});

  final double pulse;
  final bool active;

  @override
  void paint(Canvas canvas, Size size) {
    final center = size.center(Offset.zero);
    final radius = size.width / 2 - 4;
    const tickCount = 16;
    final dim = Paint()
      ..color = CopilotColors.amberDim
      ..strokeWidth = 3
      ..strokeCap = StrokeCap.round;
    final lit = Paint()
      ..color = CopilotColors.amber
      ..strokeWidth = 3
      ..strokeCap = StrokeCap.round;

    final litCount = active ? (3 + (pulse * 4)).round() : 0;
    for (var i = 0; i < tickCount; i++) {
      final angle = (i / tickCount) * 2 * 3.14159265 - 3.14159265 / 2;
      final outer = center + Offset.fromDirection(angle, radius);
      final inner = center + Offset.fromDirection(angle, radius - 9);
      final lightsUp = active && i < litCount;
      canvas.drawLine(inner, outer, lightsUp ? lit : dim);
    }
  }

  @override
  bool shouldRepaint(covariant _RingPainter oldDelegate) =>
      oldDelegate.pulse != pulse || oldDelegate.active != active;
}
