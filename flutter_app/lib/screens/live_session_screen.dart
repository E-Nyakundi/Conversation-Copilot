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

class _TranscriptEntry {
  _TranscriptEntry.finalTurn({
    required this.speaker,
    required this.text,
    required this.confidence,
  })  : kind = _EntryKind.finalTurn,
        latencyMs = null;

  _TranscriptEntry.suggestion({required this.text, required this.latencyMs})
      : kind = _EntryKind.suggestion,
        speaker = null,
        confidence = null;

  final _EntryKind kind;
  final String? speaker;
  final String text;
  final double? confidence;
  final int? latencyMs;
}

class _LiveSessionScreenState extends State<LiveSessionScreen> {
  final _urlController = TextEditingController(
    text: 'wss://145-241-102-99.sslip.io/ws/audio/',
  );
  final _socket = CopilotSocket();
  final _mic = MicCapture();
  final _entries = <_TranscriptEntry>[];
  final _scrollController = ScrollController();

  StreamSubscription? _msgSub;
  StreamSubscription? _errSub;
  StreamSubscription? _doneSub;

  String? _sessionId;
  String? _partialText;
  bool _connected = false;
  bool _recording = false;
  String _status = 'Not connected.';

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
        });
        break;
      case 'partial':
        setState(() => _partialText = data['text'] as String?);
        _scrollToEnd();
        break;
      case 'final':
        setState(() {
          _partialText = null;
          _entries.add(_TranscriptEntry.finalTurn(
            speaker: data['speaker'] as String? ?? '?',
            text: data['text'] as String? ?? '',
            confidence: (data['confidence'] as num?)?.toDouble(),
          ));
        });
        _scrollToEnd();
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
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Live Session (Phase 1)')),
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
                  itemCount: _entries.length + (_partialText != null ? 1 : 0),
                  itemBuilder: (context, index) {
                    if (index == _entries.length && _partialText != null) {
                      return Padding(
                        padding: const EdgeInsets.symmetric(vertical: 4),
                        child: Text(
                          _partialText!,
                          style: TextStyle(
                            color: Colors.grey.shade600,
                            fontStyle: FontStyle.italic,
                          ),
                        ),
                      );
                    }
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
                      child: RichText(
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
                            if (entry.latencyMs != null)
                              TextSpan(
                                text: '  (${entry.latencyMs}ms)',
                                style: TextStyle(
                                    color: Colors.grey.shade500, fontSize: 11),
                              ),
                          ],
                        ),
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
