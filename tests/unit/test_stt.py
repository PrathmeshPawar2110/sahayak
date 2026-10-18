"""The STT contract (TRD §16: every provider passes the same interface suite), then each
provider's own behaviour, the fallback chain and the factory."""

import json
import math
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
from sahayak.voice.stt import STTConfigError, create_stt_provider
from sahayak.voice.stt.base import (
    FallbackSTT,
    STTAudioError,
    STTError,
    STTProvider,
    STTTransientError,
    Transcript,
)
from sahayak.voice.stt.fake import FakeSTTProvider
from sahayak.voice.stt.sarvam import SarvamSTT
from sahayak.voice.stt.whisper import WhisperSTT

AUDIO = b"RIFF....WAVEfake"


async def _no_sleep(_seconds: float) -> None:
    return None


RETRY = RetryPolicy(attempts=2, sleep=_no_sleep)


class Server:
    """Serves scripted responses (an exception is raised as a transport error)."""

    def __init__(self, *responses: Any) -> None:
        self._responses = list(responses)
        self.requests: list[Any] = []

    def __call__(self, request: Any) -> Any:
        self.requests.append(request)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@dataclass(frozen=True)
class Harness:
    name: str
    build: Callable[[Server], STTProvider]
    ok: Callable[[str], Any]  # a successful response with this transcript
    error: Callable[[int], Any]
    connect_error: Callable[[], Exception]


def _sarvam(server: Server) -> STTProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    return SarvamSTT(api_key="k", model="saaras:v4", timeout_sec=5, retry=RETRY, http_client=client)


def _sarvam_ok(text: str, language: str | None = "hi-IN", probability: float | None = 1.0) -> Any:
    return httpx.Response(
        200,
        json={
            "request_id": "r1",
            "transcript": text,
            "language_code": language,
            "language_probability": probability,
        },
    )


def _openai_client(server: Server) -> openai.AsyncOpenAI:
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(server))
    return openai.AsyncOpenAI(api_key="k", max_retries=0, http_client=http)


def _whisper(model: str) -> Callable[[Server], STTProvider]:
    def build(server: Server) -> STTProvider:
        return WhisperSTT(
            api_key="k", model=model, timeout_sec=5, retry=RETRY, client=_openai_client(server)
        )

    return build


def _transcribe_ok(text: str, logprobs: list[float] | None = None) -> Any:
    tokens = [{"token": "t", "logprob": lp, "bytes": [116]} for lp in logprobs or [-0.1, -0.2]]
    return httpx2.Response(200, json={"text": text, "logprobs": tokens})


def _verbose_ok(text: str, language: str = "hindi", avg_logprob: float = -0.3) -> Any:
    segment = {
        "id": 0,
        "seek": 0,
        "start": 0.0,
        "end": 1.0,
        "text": text,
        "tokens": [1],
        "temperature": 0.0,
        "avg_logprob": avg_logprob,
        "compression_ratio": 1.0,
        "no_speech_prob": 0.01,
    }
    return httpx2.Response(
        200, json={"language": language, "duration": 1.0, "text": text, "segments": [segment]}
    )


def _openai_error(status: int) -> Any:
    return httpx2.Response(status, json={"error": {"message": "boom", "type": "error"}})


HARNESSES = [
    Harness(
        "sarvam",
        _sarvam,
        lambda text: _sarvam_ok(text),
        lambda status: httpx.Response(status, json={"error": {"message": "boom"}}),
        lambda: httpx.ConnectError("refused"),
    ),
    Harness(
        "whisper:gpt-4o-mini-transcribe",
        _whisper("gpt-4o-mini-transcribe"),
        lambda text: _transcribe_ok(text),
        _openai_error,
        lambda: httpx2.ConnectError("refused"),
    ),
    Harness(
        "whisper:whisper-1",
        _whisper("whisper-1"),
        lambda text: _verbose_ok(text),
        _openai_error,
        lambda: httpx2.ConnectError("refused"),
    ),
]


@pytest.fixture(params=HARNESSES, ids=lambda h: h.name)
def harness(request: pytest.FixtureRequest) -> Harness:
    value: Harness = request.param
    return value


async def test_contract_returns_a_tidy_transcript(harness: Harness) -> None:
    server = Server(harness.ok("  मेरे पास  दो एकड़\n ज़मीन है "))
    provider = harness.build(server)

    transcript = await provider.transcribe(AUDIO, mime="audio/wav", language_hint="hi")

    assert transcript.text == "मेरे पास दो एकड़ ज़मीन है"
    assert transcript.language == "hi"
    assert transcript.provider == provider.name
    assert transcript.model == provider.model
    assert transcript.confidence is None or 0 <= transcript.confidence <= 1
    assert not transcript.empty
    assert len(server.requests) == 1


async def test_contract_retries_transient_failures_once(harness: Harness) -> None:
    server = Server(harness.error(503), harness.ok("ok then"))
    assert (await harness.build(server).transcribe(AUDIO, mime="audio/wav")).text == "ok then"

    server = Server(harness.connect_error(), harness.ok("ok then"))
    assert (await harness.build(server).transcribe(AUDIO, mime="audio/wav")).text == "ok then"

    server = Server(harness.error(429), harness.error(500))
    with pytest.raises(STTTransientError):
        await harness.build(server).transcribe(AUDIO, mime="audio/wav")
    assert len(server.requests) == 2


async def test_contract_rejected_audio_is_not_retried(harness: Harness) -> None:
    server = Server(harness.error(400))
    with pytest.raises(STTAudioError):
        await harness.build(server).transcribe(AUDIO, mime="audio/wav")
    assert len(server.requests) == 1


async def test_contract_auth_errors_are_not_retried(harness: Harness) -> None:
    server = Server(harness.error(403))
    with pytest.raises(STTError) as caught:
        await harness.build(server).transcribe(AUDIO, mime="audio/wav")
    assert not isinstance(caught.value, STTTransientError | STTAudioError)
    assert len(server.requests) == 1


async def test_contract_logs_timings_but_never_the_words(harness: Harness) -> None:
    server = Server(harness.ok("my secret words"))
    with structlog.testing.capture_logs() as logs:
        await harness.build(server).transcribe(AUDIO, mime="audio/wav")
    (event,) = [e for e in logs if e["event"] == "stt_call"]
    assert event["chars"] == len("my secret words")
    assert "latency_ms" in event
    assert "my secret words" not in json.dumps(logs, default=str)


# --- Sarvam


def _field(request: httpx.Request, name: str) -> str:
    """A form field of a multipart request."""
    body = request.content.decode("utf-8", "replace")
    marker = f'name="{name}"\r\n\r\n'
    start = body.index(marker) + len(marker)
    return body[start : body.index("\r\n", start)]


async def test_sarvam_detects_the_language_and_reports_no_confidence() -> None:
    server = Server(_sarvam_ok("माझ्याकडे दोन एकर शेती आहे", "mr-IN", 1.0))
    provider = _sarvam(server)

    transcript = await provider.transcribe(AUDIO, mime="audio/ogg", language_hint="mr")

    assert transcript.language == "mr"
    assert transcript.confidence is None
    request = server.requests[0]
    assert request.headers["api-subscription-key"] == "k"
    assert _field(request, "language_code") == "unknown"
    assert _field(request, "model") == "saaras:v4"
    assert 'filename="audio"' in request.content.decode("utf-8", "replace")
    assert "audio/ogg" in request.content.decode("utf-8", "replace")


async def test_sarvam_rechecks_a_short_answer_in_another_language() -> None:
    # "हाँ" auto-detected as English "Yeah.": transcribe again in the conversation's language.
    server = Server(_sarvam_ok("Yeah.", "en-IN", 0.7), _sarvam_ok("हाँ।", "hi-IN", None))

    transcript = await _sarvam(server).transcribe(AUDIO, mime="audio/wav", language_hint="hi")

    assert transcript.text == "हाँ।"
    assert transcript.language == "hi"
    assert [_field(r, "language_code") for r in server.requests] == ["unknown", "hi-IN"]


async def test_sarvam_rechecks_an_unsure_detection() -> None:
    server = Server(
        _sarvam_ok("दोन एकर शेती आहे आणि मी शेतकरी आहे", "hi-IN", 0.6),
        _sarvam_ok("दोन एकर शेती आहे आणि मी शेतकरी आहे", None, None),
    )
    transcript = await _sarvam(server).transcribe(AUDIO, mime="audio/wav", language_hint="mr")
    assert transcript.language == "mr"
    assert len(server.requests) == 2


async def test_sarvam_follows_a_clear_switch_of_language() -> None:
    server = Server(_sarvam_ok("I am a farmer with two acres of land", "en-IN", 0.99))

    transcript = await _sarvam(server).transcribe(AUDIO, mime="audio/wav", language_hint="hi")

    assert transcript.language == "en"
    assert len(server.requests) == 1


async def test_sarvam_without_a_hint_or_with_another_language() -> None:
    server = Server(_sarvam_ok("હું ખેડૂત છું", "gu-IN", 0.98))
    transcript = await _sarvam(server).transcribe(AUDIO, mime="audio/wav")
    assert transcript.language is None
    assert len(server.requests) == 1


async def test_sarvam_empty_transcripts_are_not_rechecked() -> None:
    server = Server(_sarvam_ok("", None, None))
    transcript = await _sarvam(server).transcribe(AUDIO, mime="audio/wav", language_hint="hi")
    assert transcript.empty
    assert len(server.requests) == 1


async def test_sarvam_bad_bodies_are_errors() -> None:
    server = Server(httpx.Response(200, text="not json"))
    with pytest.raises(STTError, match="not JSON"):
        await _sarvam(server).transcribe(AUDIO, mime="audio/wav")
    server = Server(httpx.Response(200, json={"request_id": "r"}))
    with pytest.raises(STTError, match="no transcript"):
        await _sarvam(server).transcribe(AUDIO, mime="audio/wav")


async def test_sarvam_timeouts_are_transient() -> None:
    server = Server(httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"))
    with pytest.raises(STTTransientError, match="timed out"):
        await _sarvam(server).transcribe(AUDIO, mime="audio/wav")


# --- OpenAI


async def test_whisper_confidence_is_the_mean_token_probability() -> None:
    server = Server(_transcribe_ok("Yes.", [-0.1, -0.5]))
    provider = _whisper("gpt-4o-mini-transcribe")(server)

    transcript = await provider.transcribe(AUDIO, mime="audio/webm", language_hint="hi")

    assert transcript.confidence == pytest.approx(math.exp(-0.3), abs=1e-4)
    body = server.requests[0].content.decode("utf-8", "replace")
    assert 'name="language"\r\n\r\nhi' in body
    assert "logprobs" in body


async def test_whisper_without_a_hint_sends_no_language() -> None:
    server = Server(_transcribe_ok("Hello there", []))
    transcript = await _whisper("gpt-4o-mini-transcribe")(server).transcribe(
        AUDIO, mime="audio/wav"
    )
    assert 'name="language"' not in server.requests[0].content.decode("utf-8", "replace")
    assert transcript.language is None
    assert transcript.confidence is not None  # the default token list in the fake response


async def test_whisper_1_reports_the_detected_language() -> None:
    server = Server(_verbose_ok("माझ्याकडे शेती आहे", "marathi", -0.2))
    transcript = await _whisper("whisper-1")(server).transcribe(AUDIO, mime="audio/wav")
    assert transcript.language == "mr"
    assert transcript.confidence == pytest.approx(math.exp(-0.2), abs=1e-4)


# --- fallback and factory


async def test_fallback_tries_the_next_provider() -> None:
    first = FakeSTTProvider([STTAudioError("no")])
    second = FakeSTTProvider(["from the second"])
    chain = FallbackSTT([first, second])

    transcript = await chain.transcribe(AUDIO, mime="audio/wav", language_hint="en")

    assert transcript.text == "from the second"
    assert chain.name == "fake+fake"
    assert chain.model == "fake-stt"
    assert second.calls[0].language_hint == "en"


async def test_fallback_raises_the_last_error_when_all_fail() -> None:
    chain = FallbackSTT(
        [
            FakeSTTProvider([STTError("a")]),
            FakeSTTProvider([STTTransientError("b"), STTTransientError("b")]),
        ]
    )
    with pytest.raises(STTTransientError):
        await chain.transcribe(AUDIO, mime="audio/wav")
    with pytest.raises(ValueError, match="at least one"):
        FallbackSTT([])


async def test_fake_provider_scripts() -> None:
    ready = Transcript("x", "mr", 0.5, "fake", "fake-stt")
    fake = FakeSTTProvider(lambda call: ready)
    assert await fake.transcribe(AUDIO, mime="audio/wav") is ready
    with pytest.raises(AssertionError, match="ran out"):
        await FakeSTTProvider().transcribe(AUDIO, mime="audio/wav")


def _settings(**values: Any) -> Settings:
    return Settings(_env_file=None, app_env="test", **values)


def test_factory_builds_the_configured_chain() -> None:
    provider = create_stt_provider(
        _settings(sarvam_api_key="s", openai_api_key="o", stt_fallback_provider="whisper")
    )
    assert isinstance(provider, FallbackSTT)
    assert provider.name == "sarvam+whisper"

    alone = create_stt_provider(_settings(sarvam_api_key="s"))
    assert isinstance(alone, SarvamSTT)

    whisper = create_stt_provider(_settings(stt_provider="whisper", openai_api_key="o"))
    assert isinstance(whisper, WhisperSTT)
    assert whisper.model == "gpt-4o-mini-transcribe"


def test_factory_without_a_fallback_key_keeps_the_primary() -> None:
    provider = create_stt_provider(_settings(sarvam_api_key="s", stt_fallback_provider="whisper"))
    assert isinstance(provider, SarvamSTT)


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({}, "SARVAM_API_KEY"),
        ({"sarvam_api_key": "s", "sarvam_stt_model": ""}, "SARVAM_STT_MODEL"),
        ({"stt_provider": "whisper"}, "OPENAI_API_KEY"),
        ({"stt_provider": "whisper", "openai_api_key": "o", "openai_stt_model": ""}, "MODEL"),
        ({"stt_provider": "indicconformer"}, "not implemented"),
    ],
)
def test_factory_config_errors(values: dict[str, Any], message: str) -> None:
    with pytest.raises(STTConfigError, match=message):
        create_stt_provider(_settings(**values))


async def test_providers_close_their_clients() -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(Server()))
    sarvam = SarvamSTT(api_key="k", model="m", timeout_sec=1, http_client=http)
    whisper = WhisperSTT(api_key="k", model="m", timeout_sec=1, client=_openai_client(Server()))

    await FallbackSTT([sarvam, whisper, FakeSTTProvider()]).aclose()

    assert http.is_closed
    assert whisper._client.is_closed()
