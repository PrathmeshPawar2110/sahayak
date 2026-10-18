"""The TTS contract (TRD §16), each provider's request, the fallback chain and the factory."""

import base64
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import httpx2
import openai
import pytest
import structlog

from sahayak.config import Settings
from sahayak.llm.base import RetryPolicy
from sahayak.voice.audio import pcm_to_wav
from sahayak.voice.tts import TTSConfigError, create_tts_provider
from sahayak.voice.tts.base import FallbackTTS, TTSError, TTSProvider, TTSTransientError
from sahayak.voice.tts.fake import FakeTTSProvider
from sahayak.voice.tts.openai import OpenAITTS
from sahayak.voice.tts.sarvam import SarvamTTS
from tests.unit.test_stt import Server

WAV = pcm_to_wav(b"\0\0" * 1600, 16_000)


async def _no_sleep(_seconds: float) -> None:
    return None


RETRY = RetryPolicy(attempts=2, sleep=_no_sleep)


@dataclass(frozen=True)
class Harness:
    name: str
    build: Callable[[Server], TTSProvider]
    ok: Callable[[], Any]
    error: Callable[[int], Any]


def _sarvam(server: Server) -> TTSProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    return SarvamTTS(
        api_key="k",
        model="bulbul:v3",
        voice="shubh",
        timeout_sec=5,
        retry=RETRY,
        http_client=client,
    )


def _openai(server: Server) -> TTSProvider:
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(server))
    client = openai.AsyncOpenAI(api_key="k", max_retries=0, http_client=http)
    return OpenAITTS(
        api_key="k",
        model="gpt-4o-mini-tts",
        voice="alloy",
        timeout_sec=5,
        retry=RETRY,
        client=client,
    )


HARNESSES = [
    Harness(
        "sarvam",
        _sarvam,
        lambda: httpx.Response(
            200, json={"request_id": "r", "audios": [base64.b64encode(WAV).decode()]}
        ),
        lambda status: httpx.Response(status, json={"error": {"message": "boom"}}),
    ),
    Harness(
        "openai",
        _openai,
        lambda: httpx2.Response(200, content=WAV, headers={"content-type": "audio/wav"}),
        lambda status: httpx2.Response(status, json={"error": {"message": "boom"}}),
    ),
]


@pytest.fixture(params=HARNESSES, ids=lambda h: h.name)
def harness(request: pytest.FixtureRequest) -> Harness:
    value: Harness = request.param
    return value


async def test_contract_returns_audio_and_the_voice(harness: Harness) -> None:
    provider = harness.build(Server(harness.ok()))

    synthesis = await provider.synthesize("नमस्ते, मैं सहायक हूँ।", "hi")

    assert synthesis.audio == WAV
    assert synthesis.voice_id == provider.voice_id
    assert provider.voice_id == f"{provider.name}:{provider.model}:{provider.voice}"


async def test_contract_retries_transient_failures_once(harness: Harness) -> None:
    server = Server(harness.error(503), harness.ok())
    assert (await harness.build(server).synthesize("Hello", "en")).audio == WAV

    server = Server(harness.error(429), harness.error(502))
    with pytest.raises(TTSTransientError):
        await harness.build(server).synthesize("Hello", "en")
    assert len(server.requests) == 2


async def test_contract_bad_requests_are_not_retried(harness: Harness) -> None:
    server = Server(harness.error(400))
    with pytest.raises(TTSError) as caught:
        await harness.build(server).synthesize("Hello", "en")
    assert not isinstance(caught.value, TTSTransientError)
    assert len(server.requests) == 1


async def test_contract_refuses_text_over_the_limit(harness: Harness) -> None:
    server = Server()
    provider = harness.build(server)
    with pytest.raises(TTSError, match="at most"):
        await provider.synthesize("x" * (provider.max_chars + 1), "en")
    assert server.requests == []


async def test_contract_logs_timings_but_never_the_text(harness: Harness) -> None:
    with structlog.testing.capture_logs() as logs:
        await harness.build(Server(harness.ok())).synthesize("my private reply", "en")
    (event,) = [e for e in logs if e["event"] == "tts_call"]
    assert event["chars"] == len("my private reply")
    assert "my private reply" not in json.dumps(logs, default=str)


async def test_sarvam_request() -> None:
    server = Server(HARNESSES[0].ok())
    await _sarvam(server).synthesize("माझ्याकडे शेती आहे", "mr")

    request = server.requests[0]
    assert request.headers["api-subscription-key"] == "k"
    assert json.loads(request.content) == {
        "text": "माझ्याकडे शेती आहे",
        "language_code": "mr-IN",
        "model": "bulbul:v3",
        "speaker": "shubh",
        "speech_sample_rate": 24000,
        "output_audio_codec": "wav",
    }


@pytest.mark.parametrize(
    "body", [{"audios": []}, {"audios": ["not base64!"]}, {"other": 1}, "not json"]
)
async def test_sarvam_without_audio_is_an_error(body: Any) -> None:
    response = (
        httpx.Response(200, text=body) if isinstance(body, str) else httpx.Response(200, json=body)
    )
    with pytest.raises(TTSError, match="no audio"):
        await _sarvam(Server(response)).synthesize("Hello", "en")


async def test_sarvam_connection_errors_are_transient() -> None:
    server = Server(httpx.ConnectError("refused"), httpx.ReadTimeout("slow"))
    with pytest.raises(TTSTransientError):
        await _sarvam(server).synthesize("Hello", "en")


async def test_openai_request() -> None:
    server = Server(HARNESSES[1].ok())
    await _openai(server).synthesize("Hello there", "en")
    body = json.loads(server.requests[0].content)
    assert body == {
        "input": "Hello there",
        "model": "gpt-4o-mini-tts",
        "voice": "alloy",
        "response_format": "wav",
    }


async def test_openai_empty_audio_is_an_error() -> None:
    with pytest.raises(TTSError, match="no audio"):
        await _openai(Server(httpx2.Response(200, content=b""))).synthesize("Hello", "en")


async def test_fallback_uses_the_next_voice() -> None:
    first = FakeTTSProvider(failures=[TTSError("down")], max_chars=500)
    second = FakeTTSProvider(voice="backup", max_chars=300)
    chain = FallbackTTS([first, second])

    synthesis = await chain.synthesize("Hello", "en")

    assert synthesis.voice_id == second.voice_id
    assert chain.voice_id == first.voice_id
    assert chain.max_chars == 300
    with pytest.raises(ValueError, match="at least one"):
        FallbackTTS([])


async def test_fallback_raises_the_last_error() -> None:
    chain = FallbackTTS(
        [FakeTTSProvider(failures=[TTSError("a")]), FakeTTSProvider(failures=[TTSError("b")])]
    )
    with pytest.raises(TTSError, match="b"):
        await chain.synthesize("Hello", "en")


def _settings(**values: Any) -> Settings:
    return Settings(_env_file=None, app_env="test", **values)


def test_factory_builds_the_configured_chain() -> None:
    chain = create_tts_provider(
        _settings(sarvam_api_key="s", openai_api_key="o", tts_fallback_provider="openai")
    )
    assert isinstance(chain, FallbackTTS)
    assert chain.voice_id == "sarvam:bulbul:v3:shubh"
    assert isinstance(create_tts_provider(_settings(sarvam_api_key="s")), SarvamTTS)
    alone = create_tts_provider(_settings(tts_provider="openai", openai_api_key="o"))
    assert isinstance(alone, OpenAITTS)
    # A fallback without its key is left out.
    kept = create_tts_provider(_settings(sarvam_api_key="s", tts_fallback_provider="openai"))
    assert isinstance(kept, SarvamTTS)


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({}, "SARVAM_API_KEY"),
        ({"sarvam_api_key": "s", "sarvam_tts_model": ""}, "SARVAM_TTS_MODEL"),
        ({"tts_provider": "openai"}, "OPENAI_API_KEY"),
        ({"tts_provider": "openai", "openai_api_key": "o", "openai_tts_voice": ""}, "VOICE"),
        ({"tts_provider": "indic_parler"}, "not implemented"),
    ],
)
def test_factory_config_errors(values: dict[str, Any], message: str) -> None:
    with pytest.raises(TTSConfigError, match=message):
        create_tts_provider(_settings(**values))
