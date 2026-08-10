/// Platform-selecting facade for MicCapture.
///
/// Two real implementations, chosen at compile time:
///   - capture_io.dart   — native (Android/iOS/desktop), via package:record
///   - capture_web.dart  — web (Chrome etc.), via web/audio_capture.js +
///     dart:js_interop, because package:record can't stream raw PCM16 in a
///     browser (see capture_web.dart's docstring for why).
/// capture_stub.dart is an unreachable-in-practice fallback so this export
/// stays exhaustive.
///
/// dart.library.js_interop (rather than the older dart.library.html) is
/// used for the web check because it's also true when compiling to
/// WebAssembly, per Flutter's own conditional-import guidance for web
/// targets — dart.library.html is not available under Wasm.
export 'capture_stub.dart'
    if (dart.library.io) 'capture_io.dart'
    if (dart.library.js_interop) 'capture_web.dart';
