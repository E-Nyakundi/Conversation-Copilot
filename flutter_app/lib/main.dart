import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

void main() {
  runApp(const CopilotApp());
}

class CopilotApp extends StatelessWidget {
  const CopilotApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Conversation Copilot',
      theme: ThemeData(colorSchemeSeed: Colors.teal, useMaterial3: true),
      home: const PingTestScreen(),
    );
  }
}

/// Phase 0 connectivity proof: mirrors server/latency_test.html's protocol
/// against PingConsumer (server/audio/consumers.py). Client sends
/// {"client_sent_at": <ms epoch>}, server echoes it with server-side
/// timestamps added. Round trip is computed entirely on this device's own
/// clock (now - client_sent_at), so it's unaffected by any clock skew
/// between phone and server.
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
      appBar: AppBar(title: const Text('WebSocket Ping Test (Phase 0)')),
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