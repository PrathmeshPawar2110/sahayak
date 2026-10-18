"""The voice round-trip eval (M5 exit criteria): voice turns in Hindi, Marathi and English through
the real API over HTTP, timed per stage (TRD §5 latency targets, §12.5).

Each dialogue's opening (`evals/dialogues.jsonl`) is spoken by the configured TTS voice and sent
as the first turn of a new session, through `POST /voice` (upload channel) or the stream
WebSocket. Measured per turn: the server's stages (STT, agent, TTS) and, from the client, the
time from the end of the user's speech to the reply's first audio:

- upload: from the start of the upload until the reply's audio file is downloaded;
- stream: from sending `stop` until the first audio frame arrives.

Synthetic speech is clean, so the transcript's WER against the opening is a sanity check, not an
ASR score (that is the ASR eval's job).
"""

import asyncio
import base64
import json
import socket
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from statistics import fmean
from typing import Any, Literal

import httpx
import uvicorn
import websockets
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.db.models import EvalRun
from sahayak.eligibility.profile import Language
from sahayak.evals.asr import edit_distance, normalize
from sahayak.evals.dialogues import Dialogue
from sahayak.evals.latency import VOICE_TARGETS_MS, StageStats
from sahayak.voice import audio
from sahayak.voice.speech import Speaker

SUITE = "voice"
Channel = Literal["upload", "stream"]
STREAM_RATE = 16_000
STREAM_CHUNK_SEC = 0.1


@dataclass(frozen=True)
class VoiceTurnResult:
    dialogue: Dialogue
    channel: Channel
    transcript: str | None = None
    reply_kind: str | None = None
    reply_language: str | None = None
    audio_ok: bool = False
    server_ms: dict[str, int | None] = field(default_factory=dict)  # stt, agent, tts, total
    first_audio_ms: int | None = None  # client-side, from the end of speech
    error: str | None = None

    @property
    def wer(self) -> float | None:
        if self.transcript is None:
            return None
        reference = normalize(self.dialogue.opening).split()
        return edit_distance(reference, normalize(self.transcript).split()) / max(1, len(reference))


@dataclass(frozen=True)
class VoiceEval:
    turns: list[VoiceTurnResult]
    stt: str
    tts: str

    def channels(self) -> list[Channel]:
        return sorted({t.channel for t in self.turns})

    def stats(self, channel: Channel, language: Language | None = None) -> dict[str, StageStats]:
        turns = [
            t
            for t in self.turns
            if t.channel == channel and t.error is None and language in (None, t.dialogue.language)
        ]
        stages: dict[str, StageStats] = {}
        for stage in ("stt", "agent", "tts", "total"):
            values = [float(v) for t in turns if (v := t.server_ms.get(stage)) is not None]
            stages[stage] = StageStats.of(values)
        first = [float(t.first_audio_ms) for t in turns if t.first_audio_ms is not None]
        stages["first_audio"] = StageStats.of(first)
        return stages

    @property
    def errors(self) -> list[VoiceTurnResult]:
        return [t for t in self.turns if t.error is not None or not t.audio_ok]

    @property
    def gated_channel(self) -> Channel | None:
        """The channel the latency targets apply to: the stream, which the web app uses for a
        voice turn (TRD §5), or the upload when only that was measured (D-045)."""
        channels = self.channels()
        if "stream" in channels:
            return "stream"
        return channels[0] if channels else None

    def meets_targets(self, channel: Channel) -> bool:
        first = self.stats(channel)["first_audio"]
        return (
            first.p50 is not None
            and first.p95 is not None
            and first.p50 <= VOICE_TARGETS_MS["p50"]
            and first.p95 <= VOICE_TARGETS_MS["p95"]
        )

    def passed(self) -> bool:
        """No failed turn, and the first-audio targets met on the gated channel."""
        gated = self.gated_channel
        return not self.errors and gated is not None and self.meets_targets(gated)

    def metrics(self) -> dict[str, Any]:
        wers = [w for t in self.turns if (w := t.wer) is not None]
        return {
            "turns": len(self.turns),
            "errors": len(self.errors),
            "transcript_wer": fmean(wers) if wers else None,
            "reply_language_match": fmean(
                t.reply_language == t.dialogue.language for t in self.turns if t.error is None
            )
            if any(t.error is None for t in self.turns)
            else None,
            "channels": {
                channel: {
                    "all": {k: v.as_dict() for k, v in self.stats(channel).items()},
                    **{
                        lang: {k: v.as_dict() for k, v in self.stats(channel, lang).items()}
                        for lang in ("hi", "mr", "en")
                    },
                }
                for channel in self.channels()
            },
            "gated_channel": self.gated_channel,
            "meets_targets": {channel: self.meets_targets(channel) for channel in self.channels()},
            "passed": self.passed(),
        }


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@asynccontextmanager
async def serve(app: FastAPI) -> AsyncIterator[str]:
    """Run the app with uvicorn on a free local port; yields its base URL."""
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False)
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            task.result()  # raises the startup error
        await asyncio.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


async def _new_session(client: httpx.AsyncClient) -> str:
    created = await client.post("/v1/sessions", json={"channel": "web"})
    created.raise_for_status()
    session_id: str = created.json()["session_id"]
    consent = await client.post(
        f"/v1/sessions/{session_id}/consent",
        json={"purposes": ["profile"], "policy_version": "eval"},
    )
    consent.raise_for_status()
    return session_id


async def _upload_turn(
    client: httpx.AsyncClient, dialogue: Dialogue, wav: bytes, session_id: str
) -> VoiceTurnResult:
    started = time.perf_counter()
    response = await client.post(
        f"/v1/sessions/{session_id}/voice",
        files={"audio": ("opening.wav", wav, "audio/wav")},
        data={"audio_format": "mp3"},
    )
    if response.status_code != 200:
        return VoiceTurnResult(dialogue, "upload", error=f"HTTP {response.status_code}")
    body = response.json()
    url = body.get("reply_audio_url")
    audio_ok = False
    if url:
        played = await client.get(url)
        audio_ok = played.status_code == 200 and audio.sniff(played.content) == "mp3"
    first_audio_ms = round((time.perf_counter() - started) * 1000)
    latency = body["latency"]
    return VoiceTurnResult(
        dialogue,
        "upload",
        transcript=(body.get("transcript") or {}).get("text"),
        reply_kind=body["kind"],
        reply_language=body["language"],
        audio_ok=audio_ok,
        server_ms={k.removesuffix("_ms"): v for k, v in latency.items()},
        first_audio_ms=first_audio_ms if audio_ok else None,
    )


async def _stream_turn(
    base_url: str, dialogue: Dialogue, pcm: bytes, session_id: str
) -> VoiceTurnResult:
    url = base_url.replace("http://", "ws://") + f"/v1/sessions/{session_id}/stream"
    step = round(STREAM_RATE * STREAM_CHUNK_SEC) * 2
    transcript = reply_kind = reply_language = None
    first_audio_ms: int | None = None
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "start", "sample_rate": STREAM_RATE}))
        for offset in range(0, len(pcm), step):
            await ws.send(pcm[offset : offset + step])
        stopped = time.perf_counter()
        await ws.send(json.dumps({"type": "stop"}))
        chunks = 0
        while True:
            frame = json.loads(await ws.recv())
            kind = frame["type"]
            if kind == "transcript":
                transcript = frame.get("text")
            elif kind == "reply":
                reply_kind, reply_language = frame["kind"], frame["language"]
            elif kind == "audio":
                if chunks == 0:
                    first_audio_ms = round((time.perf_counter() - stopped) * 1000)
                chunks += int(audio.sniff(base64.b64decode(frame["data"])) == frame["format"])
            elif kind == "error":
                return VoiceTurnResult(dialogue, "stream", error=frame["code"])
            elif kind == "done":
                latency = frame["latency"]
                break
    return VoiceTurnResult(
        dialogue,
        "stream",
        transcript=transcript,
        reply_kind=reply_kind,
        reply_language=reply_language,
        audio_ok=chunks > 0,
        server_ms={k.removesuffix("_ms"): v for k, v in latency.items()},
        first_audio_ms=first_audio_ms,
    )


async def run_voice_eval(
    base_url: str,
    speaker: Speaker,
    dialogues: Sequence[Dialogue],
    *,
    channels: Sequence[Channel] = ("upload", "stream"),
    stt_name: str,
    on_result: Callable[[VoiceTurnResult], None] | None = None,
) -> VoiceEval:
    """One turn per dialogue and channel, one at a time (so turns don't slow each other)."""
    results = []
    async with httpx.AsyncClient(base_url=base_url, timeout=120) as client:
        for dialogue in dialogues:
            spoken = await speaker.speak(dialogue.opening, dialogue.language, "wav")
            for channel in channels:
                session_id = await _new_session(client)
                try:
                    if channel == "upload":
                        result = await _upload_turn(client, dialogue, spoken.data, session_id)
                    else:
                        pcm = await asyncio.to_thread(audio.to_pcm, spoken.data, STREAM_RATE)
                        result = await _stream_turn(base_url, dialogue, pcm, session_id)
                except (httpx.HTTPError, websockets.WebSocketException, OSError) as exc:
                    result = VoiceTurnResult(dialogue, channel, error=type(exc).__name__)
                finally:
                    await client.delete(f"/v1/sessions/{session_id}")
                results.append(result)
                if on_result:
                    on_result(result)
    return VoiceEval(turns=results, stt=stt_name, tts=speaker.voice_id)


def render_report(result: VoiceEval, *, git_sha: str | None, agent_model: str) -> str:
    metrics = result.metrics()
    lines = [
        f"# Voice round-trip eval: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        "",
        f"- **Result:** {'PASS' if result.passed() else 'FAIL'} (no failed turn, and first audio "
        f"p50 <= {VOICE_TARGETS_MS['p50']} ms and p95 <= {VOICE_TARGETS_MS['p95']} ms on the "
        f"{result.gated_channel or '-'} channel)",
        "- **Targets met:** "
        + (
            ", ".join(
                f"{channel} {'yes' if result.meets_targets(channel) else 'no'}"
                for channel in result.channels()
            )
            or "-"
        ),
        f"- **Turns:** {metrics['turns']}, **failed:** {metrics['errors']}; "
        f"**reply in the speaker's language:** {_pct(metrics['reply_language_match'])}; "
        f"**transcript WER (synthetic speech):** {_pct(metrics['transcript_wer'])}",
        f"- **STT:** {result.stt}; **TTS:** {result.tts}; **agent LLM:** {agent_model}",
        f"- **Commit:** {git_sha or 'unknown'}",
        "",
        "Times in ms. `first audio` is measured by the client from the end of speech; the other "
        "stages by the server. Upload `tts` is the whole reply, stream `tts` the first sentence.",
    ]
    for channel in result.channels():
        lines += [
            "",
            f"## {channel}",
            "",
            "| Language | Stage | p50 | p95 | max |",
            "|---|---|---|---|---|",
        ]
        for language in (None, "hi", "mr", "en"):
            stages = result.stats(channel, language)
            if not stages["total"].count:
                continue
            for stage, stats in stages.items():
                lines.append(
                    f"| {language or 'all'} | {stage} | {_ms(stats.p50)} | {_ms(stats.p95)} "
                    f"| {_ms(stats.max)} |"
                )
    lines += [
        "",
        "## Turns",
        "",
        "| Dialogue | Lang | Channel | First audio | WER | Reply | Transcript |",
        "|---|---|---|---|---|---|---|",
    ]
    for t in result.turns:
        lines.append(
            f"| {t.dialogue.id} | {t.dialogue.language} | {t.channel} | {_ms(t.first_audio_ms)} "
            f"| {_pct(t.wer)} | {t.error or f'{t.reply_kind} ({t.reply_language})'} "
            f"| {(t.transcript or '-').replace('|', '/')} |"
        )
    return "\n".join(lines) + "\n"


async def record_run(
    session: AsyncSession, result: VoiceEval, *, git_sha: str | None, config: dict[str, Any]
) -> EvalRun:
    run = EvalRun(suite=SUITE, git_sha=git_sha, config=config, metrics=result.metrics())
    session.add(run)
    await session.flush()
    return run


def _ms(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f}"


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.1%}"
