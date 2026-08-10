import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:web_socket_channel/web_socket_channel.dart';

/// Thin wrapper around the phone <-> server WebSocket protocol implemented
/// by AudioStreamConsumer (server/audio/consumers.py). Handles connecting,
/// parsing incoming JSON control messages, and sending outgoing binary
/// audio frames / the end_session control message.
class CopilotSocket {
  WebSocketChannel? _channel;
  StreamSubscription? _sub;
  final _messagesController =
      StreamController<Map<String, dynamic>>.broadcast();
  final _errorsController = StreamController<Object>.broadcast();
  final _doneController = StreamController<void>.broadcast();

  /// Parsed JSON messages from the server: session_started, partial,
  /// final, extraction, suggestion, session_ended.
  Stream<Map<String, dynamic>> get messages => _messagesController.stream;
  Stream<Object> get errors => _errorsController.stream;
  Stream<void> get onDone => _doneController.stream;

  bool get isConnected => _channel != null;

  Future<void> connect(String url) async {
    final channel = WebSocketChannel.connect(Uri.parse(url));
    // WebSocketChannel.connect() returns immediately — wait for the actual
    // handshake before declaring success, same reasoning as the Phase 0
    // ping screen.
    await channel.ready;
    _channel = channel;
    _sub = channel.stream.listen(
      (raw) {
        if (raw is String) {
          try {
            final data = jsonDecode(raw) as Map<String, dynamic>;
            _messagesController.add(data);
          } catch (e) {
            _errorsController.add('Failed to parse message: $e (raw: $raw)');
          }
        }
        // The server never sends binary frames on ws/audio/ — ignore any
        // that show up rather than crash.
      },
      onError: (error) => _errorsController.add(error),
      onDone: () => _doneController.add(null),
    );
  }

  /// Stream raw PCM16LE mono audio bytes up to the server.
  void sendAudio(Uint8List bytes) {
    _channel?.sink.add(bytes);
  }

  void sendEndSession() {
    _channel?.sink.add(jsonEncode({'type': 'end_session'}));
  }

  Future<void> disconnect() async {
    await _sub?.cancel();
    await _channel?.sink.close();
    _channel = null;
  }

  void dispose() {
    _sub?.cancel();
    _channel?.sink.close();
    _messagesController.close();
    _errorsController.close();
    _doneController.close();
  }
}
