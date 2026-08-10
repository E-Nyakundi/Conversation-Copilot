// Web mic capture for the Flutter web build.
//
// package:record (used by lib/audio/capture_io.dart on native platforms)
// can't stream raw PCM16 in a browser: browsers' MediaRecorder API always
// encodes to webm/opus regardless of the encoder you ask for, which is a
// documented limitation of the browser API itself, not something the
// package can work around. So the web path skips package:record entirely
// and does this the same way server/audio_test.html already proved works:
// getUserMedia -> AudioContext -> ScriptProcessorNode -> manual downsample
// to 16kHz -> manual Float32-to-PCM16LE conversion. This file is plain JS
// (not Dart) so that logic can be copied over verbatim instead of
// re-derived through JS interop bindings, which cuts out an entire class of
// interop-typing bugs for a browser API surface that's already deprecated
// and inconsistently typed across interop packages.
//
// Exposed as window.copilotMic = { start(onChunk), stop() } and called
// from lib/audio/capture_web.dart via dart:js_interop. onChunk receives a
// Uint8Array of PCM16LE bytes per audio callback, same shape
// MicCapture.start's onChunk expects on native.
window.copilotMic = (function () {
  let audioCtx, sourceNode, processorNode, mediaStream;
  let onChunkCallback = null;
  const TARGET_SAMPLE_RATE = 16000;

  // Linear-interpolation downsample from the browser's native sample rate
  // (commonly 44100/48000Hz) to 16kHz mono — identical to
  // server/audio_test.html's downsampleTo16k. Not a proper anti-aliased
  // resample, but the STT model tolerates it fine (already proven in
  // audio_test.html testing).
  function downsampleTo16k(buffer, inputSampleRate) {
    if (inputSampleRate === TARGET_SAMPLE_RATE) return buffer;
    const ratio = inputSampleRate / TARGET_SAMPLE_RATE;
    const outLength = Math.floor(buffer.length / ratio);
    const result = new Float32Array(outLength);
    for (let i = 0; i < outLength; i++) {
      const srcIndex = i * ratio;
      const i0 = Math.floor(srcIndex);
      const i1 = Math.min(i0 + 1, buffer.length - 1);
      const frac = srcIndex - i0;
      result[i] = buffer[i0] * (1 - frac) + buffer[i1] * frac;
    }
    return result;
  }

  function floatTo16BitPCM(float32Array) {
    const buffer = new ArrayBuffer(float32Array.length * 2);
    const view = new DataView(buffer);
    for (let i = 0; i < float32Array.length; i++) {
      const s = Math.max(-1, Math.min(1, float32Array[i]));
      view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return new Uint8Array(buffer);
  }

  // onChunk: JS function called with a Uint8Array of PCM16LE bytes per
  // audio callback (~4096 samples at the browser's native rate, downsampled
  // before this fires). Throws (rejects) if getUserMedia is denied or
  // unavailable — the caller (capture_web.dart) surfaces that as a normal
  // Dart exception via the awaited promise.
  async function start(onChunk) {
    onChunkCallback = onChunk;
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    sourceNode = audioCtx.createMediaStreamSource(mediaStream);

    // ScriptProcessorNode is deprecated in favor of AudioWorklet, but is
    // the simplest way to get raw PCM samples across browsers without
    // shipping and loading a separate AudioWorklet module file — same
    // tradeoff server/audio_test.html makes, and fine here for the same
    // reason (a still-widely-supported API, simplicity over using the
    // newer replacement).
    processorNode = audioCtx.createScriptProcessor(4096, 1, 1);
    sourceNode.connect(processorNode);
    processorNode.connect(audioCtx.destination);

    processorNode.onaudioprocess = (e) => {
      if (!onChunkCallback) return;
      const input = e.inputBuffer.getChannelData(0);
      const downsampled = downsampleTo16k(input, audioCtx.sampleRate);
      const pcm16 = floatTo16BitPCM(downsampled);
      onChunkCallback(pcm16);
    };
  }

  function stop() {
    onChunkCallback = null;
    if (processorNode) processorNode.disconnect();
    if (sourceNode) sourceNode.disconnect();
    if (mediaStream) mediaStream.getTracks().forEach((t) => t.stop());
    if (audioCtx) audioCtx.close();
    audioCtx = null;
    sourceNode = null;
    processorNode = null;
    mediaStream = null;
  }

  return { start: start, stop: stop };
})();
