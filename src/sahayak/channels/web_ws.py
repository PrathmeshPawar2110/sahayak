"""Streaming voice for the web app: `WS /v1/sessions/{id}/stream` (TRD §11, M5-T08, D-044).

Protocol (JSON text frames, plus binary frames for audio in):

Client -> server
- `{"type": "start", "sample_rate": 16000, "audio_format": "mp3"}`: begin an utterance (optional;
  these are the defaults). `audio_format` is the format of the spoken reply.
- binary frames: the recording as raw 16-bit little-endian mono PCM at `sample_rate`.
- `{"type": "stop"}`: the user stopped speaking; the server answers.

Server -> client
- `{"type": "partial_transcript", "text"}`: while the user speaks, every
  `STREAM_PARTIAL_INTERVAL_SEC` of new audio (the recording so far, transcribed again).
- `{"type": "transcript", "text", "language", "confidence"}`: the final transcript (`text`
  null when it failed).
- `{"type": "reply", ...}`: the same body as `POST /messages` returns.
- `{"type": "audio", "index", "format", "data"}`: the reply spoken, one sentence per frame
  (base64), in order, the first as soon as it is ready.
- `{"type": "done", "latency": {"stt_ms", "agent_ms", "tts_ms", "total_ms"}}`: the turn is over;
  `tts_ms` is the time until the first audio frame.
- `{"type": "error", "code", "message"}`: the utterance or the connection failed. Errors about the
  session (missing, expired, no consent) close the connection with code 1008.

One connection can carry many utterances, one at a time.
"""

import asyncio
import base64
import json
import time
import uuid
from contextlib import suppress
from typing import Any, Literal

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sahayak.agent.service import AgentService
from sahayak.api.errors import AppError
from sahayak.api.sessions import audio_url, load_session
from sahayak.api.voice import answer, check_voice_session, transcribe, transcript_response
from sahayak.config import Settings
from sahayak.eligibility.profile import Language
from sahayak.voice.audio import AudioError, AudioFormat, pcm_to_wav
from sahayak.voice.pipeline import StageTimer, TTSStatus, VoiceServices, log_voice_turn
from sahayak.voice.speech import spoken_text
from sahayak.voice.tts import TTSError

log = structlog.get_logger(__name__)

router = APIRouter()

POLICY_VIOLATION = 1008  # WebSocket close code: the session can't be used


class StartMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["start"]
    sample_rate: int = Field(16_000, ge=8_000, le=48_000)
    audio_format: AudioFormat = "mp3"


class Utterance:
    """The audio of the utterance being spoken, and its partial transcripts."""

    def __init__(
        self, websocket: WebSocket, voice: VoiceServices, language: Language | None
    ) -> None:
        self._websocket = websocket
        self._voice = voice
        self.language = language
        self.sample_rate = 16_000
        self.audio_format: AudioFormat = "mp3"
        self._pcm = bytearray()
        self._partial_at = 0
        self._partial: asyncio.Task[None] | None = None

    def start(self, message: StartMessage) -> None:
        self.reset()
        self.sample_rate = message.sample_rate
        self.audio_format = message.audio_format

    def reset(self) -> None:
        self.cancel_partial()
        self._pcm.clear()
        self._partial_at = 0

    def add(self, data: bytes) -> bool:
        """Append audio; False if the utterance got too long."""
        if len(self._pcm) + len(data) > self._voice.max_upload_bytes:
            return False
        self._pcm.extend(data)
        interval = self._voice.partial_interval_sec
        step = round(interval * self.sample_rate) * 2
        due = interval > 0 and len(self._pcm) - self._partial_at >= step
        if due and (self._partial is None or self._partial.done()):
            self._partial_at = len(self._pcm)
            self._partial = asyncio.create_task(self._send_partial(bytes(self._pcm)))
        return True

    async def take(self) -> bytes:
        """The utterance as WAV (empty if no audio), and start the next one. A partial
        transcript still on its way is dropped first, so it can't arrive after the final one."""
        partial = self._partial
        pcm = bytes(self._pcm)
        self.reset()
        if partial is not None:
            with suppress(asyncio.CancelledError):
                await partial
        return pcm_to_wav(pcm, self.sample_rate) if pcm else b""

    def cancel_partial(self) -> None:
        if self._partial is not None and not self._partial.done():
            self._partial.cancel()
        self._partial = None

    async def _send_partial(self, pcm: bytes) -> None:
        stt = self._voice.stt
        if stt is None:
            return
        try:
            transcript = await stt.transcribe(
                pcm_to_wav(pcm, self.sample_rate), mime="audio/wav", language_hint=self.language
            )
        except Exception as exc:  # a partial transcript is best effort
            log.info("stream_partial_failed", error=type(exc).__name__)
            return
        if not transcript.empty:
            await _send(self._websocket, {"type": "partial_transcript", "text": transcript.text})


async def _send(websocket: WebSocket, payload: dict[str, Any]) -> None:
    await websocket.send_text(json.dumps(payload, ensure_ascii=False, default=str))


async def _send_error(websocket: WebSocket, code: str, message: str) -> None:
    await _send(websocket, {"type": "error", "code": code, "message": message})


@router.websocket("/v1/sessions/{session_id}/stream")
async def stream(websocket: WebSocket, session_id: uuid.UUID) -> None:
    app = websocket.app
    agent: AgentService = app.state.agent
    settings: Settings = app.state.settings
    voice: VoiceServices = app.state.voice
    sessions: async_sessionmaker[AsyncSession] = app.state.session_factory
    await websocket.accept()
    try:
        async with sessions() as db:
            hint = await check_voice_session(db, agent, voice, session_id)
    except AppError as exc:
        await _send_error(websocket, exc.code, exc.message)
        await websocket.close(code=POLICY_VIOLATION)
        return

    utterance = Utterance(websocket, voice, hint)
    try:
        while True:
            received = await websocket.receive()
            if received["type"] == "websocket.disconnect":
                break
            if (data := received.get("bytes")) is not None:
                if not utterance.add(data):
                    utterance.reset()
                    await _send_error(
                        websocket, "audio_too_large", "The recording is too long. Please try again."
                    )
                continue
            control = _control(received.get("text"))
            if control is None:
                await _send_error(websocket, "bad_message", "Unknown message.")
            elif isinstance(control, StartMessage):
                utterance.start(control)
            else:
                try:
                    await _answer(
                        websocket, agent, settings, voice, sessions, session_id, utterance
                    )
                except AppError as exc:
                    await _send_error(websocket, exc.code, exc.message)
                    if exc.status_code in (404, 409, 410):
                        await websocket.close(code=POLICY_VIOLATION)
                        return
    except WebSocketDisconnect:
        pass
    finally:
        utterance.cancel_partial()


def _control(text: str | None) -> StartMessage | Literal["stop"] | None:
    try:
        payload = json.loads(text or "")
    except ValueError:
        return None
    if isinstance(payload, dict) and payload.get("type") == "stop":
        return "stop"
    try:
        return StartMessage.model_validate(payload)
    except ValidationError:
        return None


async def _answer(
    websocket: WebSocket,
    agent: AgentService,
    settings: Settings,
    voice: VoiceServices,
    sessions: async_sessionmaker[AsyncSession],
    session_id: uuid.UUID,
    utterance: Utterance,
) -> None:
    timer = StageTimer()  # from the end of speech
    audio_format = utterance.audio_format
    wav = await utterance.take()
    if not wav:
        await _send_error(websocket, "no_audio", "No audio was received.")
        return
    async with sessions() as db:
        await load_session(db, session_id)  # still there?
        await db.rollback()
        transcript = await transcribe(
            voice, wav, mime="audio/wav", language_hint=utterance.language, timer=timer
        )
        response = transcript_response(transcript)
        await _send(
            websocket,
            {"type": "transcript", **(response.model_dump() if response else {"text": None})},
        )
        turn = await answer(
            db, agent, settings, voice, session_id, transcript, utterance.language, timer=timer
        )
    language = turn.response.language
    utterance.language = language
    reply = turn.response.model_dump(mode="json")
    if voice.speaker is not None and turn.reply_id is not None:
        reply["reply_audio_url"] = audio_url(session_id, turn.reply_id, audio_format)
    await _send(websocket, {"type": "reply", **reply})

    tts: TTSStatus = "skipped"
    if voice.speaker is not None:
        began = time.perf_counter()
        try:
            text = spoken_text(turn.response.reply_text, language)
            index = 0
            async for chunk in voice.speaker.stream(text, language, audio_format):
                if index == 0:
                    timer.mark("tts", began)
                await _send(
                    websocket,
                    {
                        "type": "audio",
                        "index": index,
                        "format": chunk.format,
                        "data": base64.b64encode(chunk.data).decode("ascii"),
                    },
                )
                index += 1
            tts = "ok"
        except (TTSError, AudioError) as exc:
            log.warning("voice_tts_failed", error=type(exc).__name__)
            tts = "failed"
    total_ms = timer.total_ms()
    with suppress(RuntimeError):  # the client may have gone
        await _send(
            websocket,
            {
                "type": "done",
                "latency": {
                    "stt_ms": timer.ms.get("stt"),
                    "agent_ms": timer.ms.get("agent"),
                    "tts_ms": timer.ms.get("tts"),
                    "total_ms": total_ms,
                },
            },
        )
    log_voice_turn(
        timer,
        channel="stream",
        outcome=turn.outcome,
        language=language,
        stt_provider=transcript.provider if transcript else None,
        asr_confidence=transcript.confidence if transcript else None,
        transcript_chars=len(transcript.text) if transcript else 0,
        tts=tts,
        total_ms=total_ms,
    )
