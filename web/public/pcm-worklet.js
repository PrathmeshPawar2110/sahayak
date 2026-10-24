// AudioWorklet processor that turns the mic's Float32 samples into 16-bit PCM frames for the
// streaming voice endpoint (WS /v1/sessions/{id}/stream, D-044). Runs on the audio thread, so it
// can't import anything; it posts each render quantum's PCM16 bytes to the main thread.
class PCMCaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (channel && channel.length > 0) {
      const pcm16 = new Int16Array(channel.length);
      for (let i = 0; i < channel.length; i++) {
        const sample = Math.max(-1, Math.min(1, channel[i]));
        pcm16[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
      }
      this.port.postMessage(pcm16.buffer, [pcm16.buffer]);
    }
    return true;
  }
}

registerProcessor("pcm-capture", PCMCaptureProcessor);
