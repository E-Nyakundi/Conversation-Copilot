import 'dart:async';
import 'dart:js_interop';
import 'dart:typed_data';

/// Web (Flutter Web / Chrome) implementation — see capture.dart for the
/// platform-selection logic and capture_io.dart for the native path.
///
/// package:record (used on native) can't stream raw PCM16 in a browser:
/// browsers' MediaRecorder API always encodes to webm/opus regardless of
/// the requested encoder — a documented limitation of the browser API
/// itself, not a bug in the package. So this delegates to
/// web/audio_capture.js instead, which ports the exact getUserMedia ->
/// AudioContext -> ScriptProcessorNode -> manual downsample/PCM16 approach
/// already proven working in server/audio_test.html. See that file's
/// docstring for the full rationale.
@JS('copilotMic.start')
external JSPromise<JSAny?> _jsStart(JSFunction onChunk);

@JS('copilotMic.stop')
external void _jsStop();

class MicCapture {
  static const sampleRate = 16000;

  bool _recording = false;

  // Never read directly — flutter analyze flags this as unused, but it's
  // intentional: holding a Dart-side reference to the JS-interop closure
  // keeps it alive for the JS side to keep calling for as long as
  // recording runs. Dropping this field (letting the closure become
  // unreferenced from Dart) risks the closure being garbage collected
  // mid-recording, which would silently stop audio chunks arriving with
  // no error surfaced anywhere. Set to null in stop()/dispose() so it
  // *can* be collected once recording actually ends.
  // ignore: unused_field
  JSFunction? _jsCallback;

  bool get isRecording => _recording;

  /// On web, getUserMedia's own browser permission prompt IS the
  /// permission check — there's no separate query step like the record
  /// plugin's hasPermission() on native. A denial surfaces as an error
  /// thrown from start() instead, so this always returns true.
  Future<bool> hasPermission() async => true;

  /// Starts streaming mic audio via web/audio_capture.js. [onChunk] fires
  /// with raw PCM16LE mono bytes as they arrive, same contract as the
  /// native (capture_io.dart) implementation — pass these straight to
  /// CopilotSocket.sendAudio.
  Future<void> start(void Function(Uint8List) onChunk) async {
    void handleChunk(JSUint8Array chunk) {
      onChunk(chunk.toDart);
    }

    final jsCallback = handleChunk.toJS;
    _jsCallback = jsCallback;
    await _jsStart(jsCallback).toDart;
    _recording = true;
  }

  Future<void> stop() async {
    _jsStop();
    _jsCallback = null;
    _recording = false;
  }

  void dispose() {
    if (_recording) {
      _jsStop();
      _jsCallback = null;
      _recording = false;
    }
  }
}
