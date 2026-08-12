import 'dart:async';

import 'package:flutter/material.dart';

import '../audio/capture.dart';
import '../ws/socket.dart';

/// Phase 1: the Flutter equivalent of server/audio_test.html — mic capture
/// -> WebSocket -> live transcript, with Phase 3 suggestion bubbles layered
/// in. Talks to AudioStreamConsumer at ws/audio/ (or ws/audio/<uuid>/ to
/// resume a session).
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
        extractionConfidence = null;

  _TranscriptEntry.suggestion({
    required this.text,
    required this.latencyMs,
    required this.answerLevel,
    required this.extractionConfidence,
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
}

class _LiveSessionScreenState extends State<LiveSessionScreen> {
  final _urlController = TextEditingController(
    text: 'wss://145-241-102-99.sslip.io/ws/audio/',
  );
  final _socket = CopilotSocket();
  final _mic = MicCapture();
  final _entries = <_TranscriptEntry>[];
  final _scrollController = ScrollController();
  // Full, continuous transcript — every finalized turn appended in order,
  // plus the in-progress partial trailing at the end while someone's still
  // speaking. Distinct from `_entries` above: `_entries` is the existing
  // per-turn/per-suggestion box (each final turn its own bubble, badges,
  // suggestion cards interleaved); this is the plain, unbroken "everything
  // that's been said" view the transcript box shows.
  final List<String> _fullTranscriptLines = [];
  final _fullTranscriptScrollController = ScrollController();

  StreamSubscription? _msgSub;
  StreamSubscription? _errSub;
  StreamSubscription? _doneSub;

  String? _sessionId;
  String? _partialText;
  bool _connected = false;
  bool _recording = false;
  String _status = 'Not connected.';

  // Phase 4: defaults to Medium, matching ai/prompts.py's
  // DEFAULT_ANSWER_LEVEL — stays in sync with the server without needing
  // an explicit message until the user actually changes it.
  AnswerLevel _answerLevel = AnswerLevel.medium;

  void _setAnswerLevel(AnswerLevel level) {
    setState(() => _answerLevel = level);
    if (_connected) {
      _socket.send({'type': 'set_answer_level', 'level': level.wireValue});
    }
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
      case 'partial':
        setState(() => _partialText = data['text'] as String?);
        _scrollToEnd();
        _scrollFullTranscriptToEnd();
        break;
      case 'final':
        setState(() {
          _partialText = null;
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
        setState(() {
          _entries.add(_TranscriptEntry.suggestion(
            text: data['answer'] as String? ?? '',
            latencyMs: (data['latency_ms'] as num?)?.toInt(),
            answerLevel: data['answer_level'] as String?,
            extractionConfidence: (data['extraction_confidence'] as num?)?.toDouble(),
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

  @override
  void dispose() {
    _msgSub?.cancel();
    _errSub?.cancel();
    _doneSub?.cancel();
    _mic.dispose();
    _socket.dispose();
    _urlController.dispose();
    _scrollController.dispose();
    _fullTranscriptScrollController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Live Session')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            TextField(
              controller: _urlController,
              enabled: !_connected,
              decoration: const InputDecoration(
                border: OutlineInputBorder(),
                labelText:
                    'wss://.../ws/audio/ (append a session id to resume)',
              ),
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                ElevatedButton(
                  onPressed: _connected ? null : _connect,
                  child: const Text('Connect'),
                ),
                ElevatedButton(
                  onPressed: _connected && !_recording ? _startMic : null,
                  child: const Text('Start Mic'),
                ),
                ElevatedButton(
                  onPressed: _recording ? _stopMic : null,
                  child: const Text('Stop Mic'),
                ),
                ElevatedButton(
                  onPressed: _connected ? _endSession : null,
                  child: const Text('End Session'),
                ),
                ElevatedButton(
                  onPressed: _connected ? _disconnect : null,
                  child: const Text('Disconnect'),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Text(_status, style: const TextStyle(fontWeight: FontWeight.bold)),
            const SizedBox(height: 8),
            Row(
              children: [
                const Text('Answer length: ',
                    style: TextStyle(fontWeight: FontWeight.w600)),
                const SizedBox(width: 4),
                SegmentedButton<AnswerLevel>(
                  segments: AnswerLevel.values
                      .map((l) => ButtonSegment(value: l, label: Text(l.label)))
                      .toList(),
                  selected: {_answerLevel},
                  onSelectionChanged: (selection) => _setAnswerLevel(selection.first),
                  showSelectedIcon: false,
                ),
              ],
            ),
            const SizedBox(height: 12),
            const Text('Full transcript',
                style: TextStyle(fontWeight: FontWeight.w600, fontSize: 13)),
            const SizedBox(height: 4),
            Expanded(
              flex: 2,
              child: Container(
                decoration: BoxDecoration(
                  border: Border.all(color: Colors.grey.shade300),
                  borderRadius: BorderRadius.circular(8),
                ),
                padding: const EdgeInsets.all(8),
                child: SingleChildScrollView(
                  controller: _fullTranscriptScrollController,
                  child: RichText(
                    text: TextSpan(
                      style: DefaultTextStyle.of(context)
                          .style
                          .copyWith(height: 1.4),
                      children: [
                        TextSpan(text: _fullTranscriptLines.join('\n')),
                        if (_partialText != null)
                          TextSpan(
                            text: (_fullTranscriptLines.isEmpty ? '' : '\n') +
                                _partialText!,
                            style: TextStyle(
                              color: Colors.grey.shade600,
                              fontStyle: FontStyle.italic,
                            ),
                          ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
            const SizedBox(height: 12),
            const Text('Extracted questions & suggestions',
                style: TextStyle(fontWeight: FontWeight.w600, fontSize: 13)),
            const SizedBox(height: 4),
            Expanded(
              flex: 3,
              child: Container(
                decoration: BoxDecoration(
                  border: Border.all(color: Colors.grey.shade300),
                  borderRadius: BorderRadius.circular(8),
                ),
                padding: const EdgeInsets.all(8),
                child: ListView.builder(
                  controller: _scrollController,
                  itemCount: _entries.length,
                  itemBuilder: (context, index) {
                    final entry = _entries[index];
                    if (entry.kind == _EntryKind.finalTurn) {
                      final conf =
                          entry.confidence?.toStringAsFixed(2) ?? 'n/a';
                      return Padding(
                        padding: const EdgeInsets.symmetric(vertical: 4),
                        child: RichText(
                          text: TextSpan(
                            style: DefaultTextStyle.of(context).style,
                            children: [
                              TextSpan(
                                text: '${entry.speaker}: ',
                                style:
                                    const TextStyle(fontWeight: FontWeight.bold),
                              ),
                              TextSpan(text: entry.text),
                              TextSpan(
                                text: '  (confidence: $conf)',
                                style: TextStyle(
                                    color: Colors.grey.shade500, fontSize: 11),
                              ),
                            ],
                          ),
                        ),
                      );
                    }
                    final levelLabel = entry.answerLevel != null
                        ? entry.answerLevel![0].toUpperCase() +
                            entry.answerLevel!.substring(1)
                        : null;
                    // Extraction confidence, not model confidence — see
                    // the note on _TranscriptEntry.extractionConfidence.
                    final confLabel = entry.extractionConfidence != null
                        ? 'extraction ${(entry.extractionConfidence! * 100).round()}%'
                        : null;
                    return Container(
                      margin: const EdgeInsets.only(left: 16, top: 4, bottom: 8),
                      padding:
                          const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                      decoration: BoxDecoration(
                        color: const Color(0xFFEEF6FF),
                        border: const Border(
                            left: BorderSide(color: Color(0xFF4A90D9), width: 3)),
                        borderRadius: BorderRadius.circular(4),
                      ),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          RichText(
                            text: TextSpan(
                              style: DefaultTextStyle.of(context)
                                  .style
                                  .copyWith(fontSize: 13),
                              children: [
                                const TextSpan(
                                  text: '💡 Suggested answer: ',
                                  style: TextStyle(
                                      fontWeight: FontWeight.bold,
                                      color: Color(0xFF2B5F94)),
                                ),
                                TextSpan(text: entry.text),
                              ],
                            ),
                          ),
                          const SizedBox(height: 4),
                          Wrap(
                            spacing: 8,
                            children: [
                              if (levelLabel != null)
                                Text('level: $levelLabel',
                                    style: TextStyle(
                                        color: Colors.grey.shade500,
                                        fontSize: 11)),
                              if (confLabel != null)
                                Text(confLabel,
                                    style: TextStyle(
                                        color: Colors.grey.shade500,
                                        fontSize: 11)),
                              if (entry.latencyMs != null)
                                Text('${entry.latencyMs}ms',
                                    style: TextStyle(
                                        color: Colors.grey.shade500,
                                        fontSize: 11)),
                            ],
                          ),
                        ],
                      ),
                    );
                  },
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
