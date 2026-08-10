import 'dart:async';
import 'dart:typed_data';

import 'package:record/record.dart';

/// Native (Android/iOS/desktop) implementation — see capture.dart for the
/// platform-selection logic and capture_web.dart for the browser path,
/// which can't use this same approach (package:record can't stream raw
/// PCM16 in a browser; see capture_web.dart's docstring for why).
///
/// Wraps the `record` package to stream raw PCM16LE mono audio at 16kHz —
/// the exact format AudioStreamConsumer expects (server/audio/consumers.py,
/// audio/vad.py). On-device the `record` plugin captures directly at the
/// target sample rate, so no resampling is needed here.
class MicCapture {
  static const sampleRate = 16000;

  final _recorder = AudioRecorder();
  StreamSubscription<Uint8List>? _sub;

  bool get isRecording => _sub != null;

  /// Checks (and, on first call, prompts for) the mic permission.
  Future<bool> hasPermission() => _recorder.hasPermission();

  /// Starts streaming mic audio. [onChunk] fires with raw PCM16LE mono
  /// bytes as they arrive — pass these straight to CopilotSocket.sendAudio.
  Future<void> start(void Function(Uint8List) onChunk) async {
    if (!await hasPermission()) {
      throw StateError('Microphone permission denied.');
    }
    final stream = await _recorder.startStream(
      const RecordConfig(
        encoder: AudioEncoder.pcm16bits,
        sampleRate: sampleRate,
        numChannels: 1,
      ),
    );
    _sub = stream.listen(onChunk);
  }

  Future<void> stop() async {
    await _sub?.cancel();
    _sub = null;
    if (await _recorder.isRecording()) {
      await _recorder.stop();
    }
  }

  void dispose() {
    _sub?.cancel();
    _recorder.dispose();
  }
}
