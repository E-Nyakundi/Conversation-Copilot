import 'dart:typed_data';

/// Fallback for any platform that's neither dart:io (native) nor
/// dart.library.js_interop (web/wasm) — shouldn't be reachable on any
/// Flutter target this app actually ships to, but keeps capture.dart's
/// conditional export exhaustive rather than leaving a silent gap.
class MicCapture {
  static const sampleRate = 16000;

  bool get isRecording => false;

  Future<bool> hasPermission() async => false;

  Future<void> start(void Function(Uint8List) onChunk) async {
    throw UnsupportedError('MicCapture is not supported on this platform.');
  }

  Future<void> stop() async {}

  void dispose() {}
}
