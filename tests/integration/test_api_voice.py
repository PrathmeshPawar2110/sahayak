"""Voice turns end to end on real Postgres: upload, spoken replies and the stream WebSocket, with
fake speech providers, a fake LLM and an in-memory TTS cache (M5-T06, M5-T08)."""

import base64
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fakeredis import FakeAsyncRedis
from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.testclient import TestClient

from sahayak.agent.checkpoint import checkpoint_serializer
from sahayak.agent.graph import build_graph
from sahayak.agent.replies import message
from sahayak.agent.service import AgentService
from sahayak.api.app import create_app
from sahayak.config import Settings
from sahayak.db.models import Message, MessageRole, Session
from sahayak.voice import audio
from sahayak.voice.audio import pcm_to_wav
from sahayak.voice.pipeline import VoiceServices
from sahayak.voice.speech import Speaker
from sahayak.voice.stt.base import STTError, Transcript
from sahayak.voice.stt.fake import FakeSTTProvider, STTCall, STTReply
from sahayak.voice.tts.base import TTSError
from sahayak.voice.tts.fake import FakeTTSProvider
from tests.agent_support import AgentFakeLLM, agent_llm, make_deps, understood

Client = httpx.AsyncClient
WAV = pcm_to_wav(b"\0\0" * 8000, 16_000)


class Voice:
    """The fake speech side of the app: queue transcripts, inspect what was synthesised."""

    def __init__(self) -> None:
        self.transcripts: list[STTReply] = []
        self.stt = FakeSTTProvider(self._next)
        self.tts = FakeTTSProvider()
        self.redis = FakeAsyncRedis()
        self.speaker = Speaker(self.tts, self.redis, ttl_sec=60)

    def _next(self, call: STTCall) -> STTReply:
        return self.transcripts.pop(0) if self.transcripts else "partial words"

    def services(self, **overrides: Any) -> VoiceServices:
        values: dict[str, Any] = {
            "stt": self.stt,
            "speaker": self.speaker,
            "max_upload_bytes": 200_000,
            "partial_interval_sec": 0.0,
        }
        values.update(overrides)
        return VoiceServices(**values)


def _app(settings: Settings, llm: AgentFakeLLM, services: VoiceServices) -> FastAPI:
    saver = InMemorySaver(serde=checkpoint_serializer())

    @asynccontextmanager
    async def agent(
        _settings: Settings, _sessions: async_sessionmaker[AsyncSession], _redis: Redis
    ) -> AsyncIterator[AgentService]:
        yield AgentService(build_graph(saver), make_deps(llm))

    return create_app(settings, agent=agent, voice=lambda _settings, _redis: services)


@pytest.fixture
def llm() -> AgentFakeLLM:
    return agent_llm()


@pytest.fixture
def voice() -> Voice:
    return Voice()


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncIterator[Client]:
    async with LifespanManager(app) as manager:
        transport = httpx.ASGITransport(app=manager.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture
async def cleanup(db_engine: AsyncEngine) -> AsyncIterator[None]:
    started = datetime.now(UTC)
    yield
    async with db_engine.begin() as connection:  # the API commits for real: clean up
        await connection.execute(delete(Session).where(Session.created_at >= started))


async def _session(api: Client, *, consent: bool = True, **body: Any) -> str:
    response = await api.post("/v1/sessions", json=body)
    session_id: str = response.json()["session_id"]
    if consent:
        granted = await api.post(
            f"/v1/sessions/{session_id}/consent",
            json={"purposes": ["profile"], "policy_version": "2026-10"},
        )
        assert granted.status_code == 200, granted.text
    return session_id


async def _speak(api: Client, session_id: str, data: bytes = WAV, **form: str) -> httpx.Response:
    return await api.post(
        f"/v1/sessions/{session_id}/voice",
        files={"audio": ("recording.wav", data, "audio/wav")},
        data=form,
    )


async def _rows(engine: AsyncEngine, session_id: str) -> list[Message]:
    async with AsyncSession(engine) as db:
        rows = await db.scalars(
            select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
        )
        return list(rows.all())


async def test_a_voice_turn_returns_transcript_reply_and_audio(
    database_url: str,
    settings: Settings,
    llm: AgentFakeLLM,
    voice: Voice,
    db_engine: AsyncEngine,
    cleanup: None,
) -> None:
    llm.queue.append(understood(facts={"age": "65", "occupation": "farmer"}, language="hi"))
    voice.transcripts.append(Transcript("मैं 65 साल का किसान हूँ", "hi", 0.9, "fake", "fake-stt"))
    async with _client(_app(settings, llm, voice.services())) as api:
        session_id = await _session(api, language="hi")
        response = await _speak(api, session_id)
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["transcript"] == {
            "text": "मैं 65 साल का किसान हूँ",
            "language": "hi",
            "confidence": 0.9,
        }
        assert body["kind"] == "question"
        assert body["language"] == "hi"
        assert set(body["latency"]) == {"stt_ms", "agent_ms", "tts_ms", "total_ms"}
        assert body["latency"]["tts_ms"] is not None
        assert voice.stt.calls[0].language_hint == "hi"
        assert voice.stt.calls[0].mime == "audio/wav"
        spoken = len(voice.tts.calls)
        assert spoken >= 1

        url = body["reply_audio_url"]
        assert url.endswith("/audio?format=mp3")
        played = await api.get(url)
        assert played.status_code == 200
        assert played.headers["content-type"] == "audio/mpeg"
        assert played.headers["cache-control"] == "private"
        assert audio.sniff(played.content) == "mp3"
        assert len(voice.tts.calls) == spoken  # served from the cache

        whatsapp = await api.get(url.replace("format=mp3", "format=ogg"))
        assert audio.sniff(whatsapp.content) == "ogg"

    user, reply = await _rows(db_engine, session_id)
    assert (user.role, user.text, user.asr_confidence) == (
        MessageRole.USER,
        "मैं 65 साल का किसान हूँ",
        0.9,
    )
    assert user.audio_ref is None  # recordings are not kept
    assert url.split("/")[5] == str(reply.id)


async def test_text_turns_also_get_a_spoken_reply(
    database_url: str, settings: Settings, llm: AgentFakeLLM, voice: Voice, cleanup: None
) -> None:
    llm.queue.append(understood("smalltalk"))
    async with _client(_app(settings, llm, voice.services())) as api:
        session_id = await _session(api)
        body = (
            await api.post(f"/v1/sessions/{session_id}/messages", json={"text": "hello"})
        ).json()
        assert voice.tts.calls == []  # synthesised only when played
        played = await api.get(body["reply_audio_url"])
        assert played.status_code == 200
        assert [c.text for c in voice.tts.calls] == voice.speaker.split(message("smalltalk", "en"))

    async with _client(_app(settings, llm, voice.services(speaker=None))) as api:
        session_id = await _session(api)
        body = (
            await api.post(f"/v1/sessions/{session_id}/messages", json={"text": "hello"})
        ).json()
        assert body["reply_audio_url"] is None


async def test_voice_needs_consent_before_any_transcription(
    database_url: str, settings: Settings, llm: AgentFakeLLM, voice: Voice, cleanup: None
) -> None:
    async with _client(_app(settings, llm, voice.services())) as api:
        session_id = await _session(api, consent=False)
        response = await _speak(api, session_id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "consent_required"
    assert voice.stt.calls == []


@pytest.mark.parametrize(
    ("data", "status", "code"),
    [
        (b"this is not audio", 415, "unsupported_audio"),
        (b"RIFF" + b"\0" * 4 + b"WAVE" + b"\0" * 300_000, 413, "audio_too_large"),
    ],
    ids=["not-audio", "too-large"],
)
async def test_bad_recordings_are_refused(
    database_url: str,
    settings: Settings,
    llm: AgentFakeLLM,
    voice: Voice,
    cleanup: None,
    data: bytes,
    status: int,
    code: str,
) -> None:
    async with _client(_app(settings, llm, voice.services())) as api:
        session_id = await _session(api)
        response = await _speak(api, session_id, data)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert voice.stt.calls == []


@pytest.mark.parametrize(
    ("reply", "key", "kind"),
    [
        (STTError("down"), "audio_failed", "error"),
        ("   ", "no_speech", "notice"),
    ],
)
async def test_unusable_recordings_get_a_spoken_notice(
    database_url: str,
    settings: Settings,
    llm: AgentFakeLLM,
    voice: Voice,
    db_engine: AsyncEngine,
    cleanup: None,
    reply: STTReply,
    key: str,
    kind: str,
) -> None:
    voice.transcripts.append(reply)
    async with _client(_app(settings, llm, voice.services())) as api:
        session_id = await _session(api, language="mr")
        body = (await _speak(api, session_id)).json()
        assert body["reply_text"] == message(key, "mr")
        assert body["kind"] == kind
        assert body["language"] == "mr"
        assert (await api.get(body["reply_audio_url"])).status_code == 200
    assert llm.calls == []  # the agent never ran
    if key == "audio_failed":
        assert body["transcript"] is None
    (notice,) = await _rows(db_engine, session_id)
    assert notice.role == MessageRole.ASSISTANT


async def test_a_tts_failure_still_returns_the_text_reply(
    database_url: str, settings: Settings, llm: AgentFakeLLM, voice: Voice, cleanup: None
) -> None:
    llm.queue.append(understood("smalltalk"))
    voice.transcripts.append("hello")
    voice.tts = FakeTTSProvider(failures=[TTSError("down")] * 10)
    services = voice.services(speaker=Speaker(voice.tts, voice.redis, ttl_sec=60))
    async with _client(_app(settings, llm, services)) as api:
        session_id = await _session(api)
        body = (await _speak(api, session_id)).json()
        assert body["reply_text"] == message("smalltalk", "en")
        assert body["reply_audio_url"] is None
        assert body["latency"]["tts_ms"] is not None


async def test_voice_unavailable_without_speech_to_text(
    database_url: str, settings: Settings, llm: AgentFakeLLM, voice: Voice, cleanup: None
) -> None:
    async with _client(_app(settings, llm, voice.services(stt=None, speaker=None))) as api:
        session_id = await _session(api)
        response = await _speak(api, session_id)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "voice_unavailable"


async def test_reply_audio_belongs_to_its_session(
    database_url: str, settings: Settings, llm: AgentFakeLLM, voice: Voice, cleanup: None
) -> None:
    llm.queue.extend([understood("smalltalk"), understood("smalltalk")])
    async with _client(_app(settings, llm, voice.services())) as api:
        mine = await _session(api)
        theirs = await _session(api)
        url = (await api.post(f"/v1/sessions/{mine}/messages", json={"text": "hi"})).json()[
            "reply_audio_url"
        ]
        stolen = await api.get(url.replace(mine, theirs))
        assert stolen.status_code == 404
        assert stolen.json()["error"]["code"] == "message_not_found"
        missing = await api.get(f"/v1/sessions/{mine}/messages/{uuid.uuid4()}/audio")
        assert missing.status_code == 404
        bad_format = await api.get(url.replace("format=mp3", "format=flac"))
        assert bad_format.status_code == 422


async def test_deleting_the_session_forgets_its_cached_audio(
    database_url: str, settings: Settings, llm: AgentFakeLLM, voice: Voice, cleanup: None
) -> None:
    llm.queue.append(understood("smalltalk"))
    voice.transcripts.append("hello")
    async with _client(_app(settings, llm, voice.services())) as api:
        session_id = await _session(api)
        await _speak(api, session_id)
        assert await voice.redis.keys("tts:*")
        assert (await api.delete(f"/v1/sessions/{session_id}")).status_code == 204
    assert await voice.redis.keys("tts:*") == []


# --- the stream WebSocket (sync: Starlette's TestClient runs the app in its own thread)


@pytest.fixture
def ws_client(
    database_url: str, settings: Settings, llm: AgentFakeLLM, voice: Voice
) -> Iterator[TestClient]:
    with TestClient(_app(settings, llm, voice.services(partial_interval_sec=0.5))) as client:
        sessions: list[str] = []
        client.sessions = sessions  # type: ignore[attr-defined]
        yield client
        for session_id in sessions:
            client.delete(f"/v1/sessions/{session_id}")


def _ws_session(client: TestClient, *, consent: bool = True) -> str:
    session_id: str = client.post("/v1/sessions", json={}).json()["session_id"]
    client.sessions.append(session_id)  # type: ignore[attr-defined]
    if consent:
        client.post(
            f"/v1/sessions/{session_id}/consent",
            json={"purposes": ["profile"], "policy_version": "2026-10"},
        )
    return session_id


def _receive_until(ws: Any, kind: str) -> list[dict[str, Any]]:
    received = []
    while True:
        frame = json.loads(ws.receive_text())
        received.append(frame)
        if frame["type"] in (kind, "error"):
            return received


def test_the_stream_sends_partials_transcript_reply_and_audio(
    ws_client: TestClient, llm: AgentFakeLLM, voice: Voice
) -> None:
    llm.queue.append(understood(facts={"age": "45"}))
    voice.transcripts.extend(["I am", "I am 45", "I am 45 years old"])
    session_id = _ws_session(ws_client)
    second = b"\0\0" * 16_000

    with ws_client.websocket_connect(f"/v1/sessions/{session_id}/stream") as ws:
        ws.send_text(json.dumps({"type": "start", "sample_rate": 16000, "audio_format": "wav"}))
        ws.send_bytes(second)  # 1 s: a partial transcript is due every 0.5 s
        partial = json.loads(ws.receive_text())
        assert partial == {"type": "partial_transcript", "text": "I am"}
        ws.send_bytes(second)
        assert json.loads(ws.receive_text())["text"] == "I am 45"
        ws.send_text(json.dumps({"type": "stop"}))
        frames = _receive_until(ws, "done")

    kinds = [f["type"] for f in frames]
    assert kinds[0] == "transcript"
    assert frames[0]["text"] == "I am 45 years old"
    reply = frames[kinds.index("reply")]
    assert reply["kind"] == "question"
    assert reply["reply_audio_url"].endswith("format=wav")
    chunks = [f for f in frames if f["type"] == "audio"]
    assert [c["index"] for c in chunks] == list(range(len(chunks)))
    assert chunks
    assert all(audio.sniff(base64.b64decode(c["data"])) == "wav" for c in chunks)
    done = frames[-1]
    assert done["type"] == "done"
    assert done["latency"]["tts_ms"] is not None
    # The final transcript covered the whole utterance.
    final_call = voice.stt.calls[-1]
    assert len(final_call.audio) == len(pcm_to_wav(second * 2, 16_000))


def test_the_stream_refuses_a_session_without_consent(ws_client: TestClient) -> None:
    session_id = _ws_session(ws_client, consent=False)
    with ws_client.websocket_connect(f"/v1/sessions/{session_id}/stream") as ws:
        frame = json.loads(ws.receive_text())
        assert frame["type"] == "error"
        assert frame["code"] == "consent_required"
        closed = ws.receive()
        assert closed["type"] == "websocket.close"
        assert closed["code"] == 1008


def test_the_stream_rejects_bad_messages_and_empty_utterances(ws_client: TestClient) -> None:
    session_id = _ws_session(ws_client)
    with ws_client.websocket_connect(f"/v1/sessions/{session_id}/stream") as ws:
        ws.send_text("not json")
        assert json.loads(ws.receive_text())["code"] == "bad_message"
        ws.send_text(json.dumps({"type": "start", "sample_rate": 1}))
        assert json.loads(ws.receive_text())["code"] == "bad_message"
        ws.send_text(json.dumps({"type": "stop"}))
        assert json.loads(ws.receive_text())["code"] == "no_audio"
        ws.send_bytes(b"\0" * 250_000)
        assert json.loads(ws.receive_text())["code"] == "audio_too_large"


def test_one_connection_carries_several_utterances(
    ws_client: TestClient, llm: AgentFakeLLM, voice: Voice
) -> None:
    llm.queue.extend([understood("smalltalk", language="hi"), understood("smalltalk")])
    voice.transcripts.extend(["नमस्ते", "hello"])
    session_id = _ws_session(ws_client)
    with ws_client.websocket_connect(f"/v1/sessions/{session_id}/stream") as ws:
        for _ in range(2):
            ws.send_bytes(b"\0\0" * 4000)
            ws.send_text(json.dumps({"type": "stop"}))
            frames = _receive_until(ws, "done")
            assert frames[-1]["type"] == "done"
    # The second utterance was transcribed with the language the first reply used.
    assert [c.language_hint for c in voice.stt.calls] == [None, "hi"]
