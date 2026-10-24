import { useCallback, useEffect, useRef, useState } from "react";
import type { MessageResponse } from "../api/types";

export type VoiceState = "idle" | "connecting" | "listening" | "thinking" | "speaking" | "error";

interface TranscriptEvent {
  text: string | null;
  language?: string | null;
  confidence?: number | null;
}

interface UseVoiceStreamOptions {
  wsUrl: string;
  onPartialTranscript?: (text: string) => void;
  onFinalTranscript?: (transcript: TranscriptEvent) => void;
  onReply?: (reply: MessageResponse) => void;
  onError?: (message: string) => void;
}

interface AudioChunk {
  format: string;
  data: string;
}

const SAMPLE_RATE = 16_000;

/** Records the mic as raw PCM16 and streams it over the WS voice protocol (D-044), queueing the
 * reply's audio chunks for gapless sequential playback as they arrive. */
export function useVoiceStream({
  wsUrl,
  onPartialTranscript,
  onFinalTranscript,
  onReply,
  onError,
}: UseVoiceStreamOptions) {
  const [state, setState] = useState<VoiceState>("idle");
  const wsRef = useRef<WebSocket | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const workletRef = useRef<AudioWorkletNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const playerRef = useRef<AudioPlayer | null>(null);

  useEffect(() => {
    return () => {
      stopCapture();
      wsRef.current?.close();
      playerRef.current?.stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stopCapture = useCallback(() => {
    workletRef.current?.disconnect();
    workletRef.current = null;
    for (const track of streamRef.current?.getTracks() ?? []) track.stop();
    streamRef.current = null;
    void audioContextRef.current?.close();
    audioContextRef.current = null;
  }, []);

  const handleServerMessage = useCallback(
    (raw: string) => {
      const payload = JSON.parse(raw) as { type: string } & Record<string, unknown>;
      switch (payload.type) {
        case "partial_transcript":
          onPartialTranscript?.(String(payload.text ?? ""));
          break;
        case "transcript":
          setState("thinking");
          onFinalTranscript?.(payload as unknown as TranscriptEvent);
          break;
        case "reply":
          setState("speaking");
          onReply?.(payload as unknown as MessageResponse);
          break;
        case "audio":
          playerRef.current?.enqueue(payload as unknown as AudioChunk);
          break;
        case "done":
          setState("idle");
          break;
        case "error":
          setState("error");
          onError?.(String(payload.message ?? "error"));
          break;
      }
    },
    [onPartialTranscript, onFinalTranscript, onReply, onError],
  );

  const ensureSocket = useCallback((): Promise<WebSocket> => {
    const existing = wsRef.current;
    if (existing && existing.readyState === WebSocket.OPEN) return Promise.resolve(existing);
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(wsUrl);
      ws.binaryType = "arraybuffer";
      ws.onopen = () => resolve(ws);
      ws.onerror = () => reject(new Error("websocket_failed"));
      ws.onmessage = (event: MessageEvent<string>) => handleServerMessage(event.data);
      ws.onclose = () => {
        wsRef.current = null;
      };
      wsRef.current = ws;
    });
  }, [wsUrl, handleServerMessage]);

  const start = useCallback(async () => {
    setState("connecting");
    playerRef.current = new AudioPlayer();
    try {
      const ws = await ensureSocket();
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 } });
      streamRef.current = stream;
      const context = new AudioContext({ sampleRate: SAMPLE_RATE });
      audioContextRef.current = context;
      await context.audioWorklet.addModule("/pcm-worklet.js");
      const source = context.createMediaStreamSource(stream);
      const worklet = new AudioWorkletNode(context, "pcm-capture");
      worklet.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        if (ws.readyState === WebSocket.OPEN) ws.send(event.data);
      };
      source.connect(worklet);
      workletRef.current = worklet;
      ws.send(JSON.stringify({ type: "start", sample_rate: context.sampleRate }));
      setState("listening");
    } catch {
      setState("error");
      onError?.("mic_permission");
      stopCapture();
    }
  }, [ensureSocket, onError, stopCapture]);

  const stop = useCallback(() => {
    stopCapture();
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "stop" }));
      setState("thinking");
    } else {
      setState("idle");
    }
  }, [stopCapture]);

  return { state, start, stop };
}

/** Plays the reply's TTS chunks in order, one `HTMLAudioElement` at a time, starting as soon as
 * the first chunk arrives instead of waiting for the whole reply. */
class AudioPlayer {
  private queue: AudioChunk[] = [];
  private playing = false;
  private stopped = false;
  private current: HTMLAudioElement | null = null;

  enqueue(chunk: AudioChunk): void {
    if (this.stopped) return;
    this.queue.push(chunk);
    if (!this.playing) this.playNext();
  }

  stop(): void {
    this.stopped = true;
    this.current?.pause();
    this.queue = [];
  }

  private playNext(): void {
    const chunk = this.queue.shift();
    if (!chunk) {
      this.playing = false;
      return;
    }
    this.playing = true;
    const mime = chunk.format === "wav" ? "audio/wav" : chunk.format === "ogg" ? "audio/ogg" : "audio/mpeg";
    const bytes = base64ToBytes(chunk.data);
    const url = URL.createObjectURL(new Blob([bytes.buffer as ArrayBuffer], { type: mime }));
    const audio = new Audio(url);
    this.current = audio;
    audio.onended = () => {
      URL.revokeObjectURL(url);
      this.playNext();
    };
    audio.onerror = () => {
      URL.revokeObjectURL(url);
      this.playNext();
    };
    void audio.play().catch(() => this.playNext());
  }
}

function base64ToBytes(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}
