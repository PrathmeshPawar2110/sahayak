from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from fakeredis import FakeAsyncRedis
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from sahayak.voice import audio
from sahayak.voice.speech import MIN_SENTENCE_CHARS, Speaker, sentences, spoken_text
from sahayak.voice.tts.base import FallbackTTS, TTSError, TTSTransientError
from sahayak.voice.tts.fake import FakeTTSProvider

REPLY = (
    "Here is what I found.\n\n"
    "• Farmers with land get Rs 6000 a year income support. [1][2]\n"
    "• Pension of Rs 3000 a month. [3]\n\n"
    "⚠️ Not yet verified: my check for New Housing uses rules our team has not reviewed yet. "
    "Please confirm at https://example.gov.in/new-housing.\n\n"
    "Sources:\n"
    "[1] Farm Aid: Benefits, https://example.gov.in/farm.pdf (last verified 17-10-2026)\n"
    "[2] Farm Aid: Eligibility, https://example.gov.in/farm.pdf"
)


def test_spoken_text_drops_markers_sources_and_urls() -> None:
    spoken = spoken_text(REPLY, "en")

    assert spoken == (
        "Here is what I found.\n"
        "Farmers with land get Rs 6000 a year income support.\n"
        "Pension of Rs 3000 a month.\n"
        "Not yet verified: my check for New Housing uses rules our team has not reviewed yet. "
        "Please confirm at the official website."
    )


def test_spoken_text_in_hindi_uses_the_hindi_heading() -> None:
    reply = "मुझे यह मिला। [1]\n\nस्रोत:\n[1] योजना: लाभ, https://example.gov.in/a.pdf"
    assert spoken_text(reply, "hi") == "मुझे यह मिला।"


def test_sentences_split_on_stops_and_newlines_and_join_short_ones() -> None:
    text = "Yes. That is fine for you today! Do you have land? हाँ। आपकी उम्र क्या है?\nOk"

    assert sentences(text, 1000) == [
        "Yes. That is fine for you today!",
        "Do you have land? हाँ। आपकी उम्र क्या है? Ok",
    ]
    assert all(len(s) >= MIN_SENTENCE_CHARS for s in sentences(text, 1000)[:-1])


def test_sentences_wrap_long_ones_at_spaces() -> None:
    long = " ".join(["word"] * 50)  # 249 characters, no full stop
    pieces = sentences(long, 60)
    assert all(len(p) <= 60 for p in pieces)
    assert " ".join(pieces) == long
    assert sentences("x" * 130, 60) == ["x" * 60, "x" * 60, "x" * 10]
    assert sentences("  \n ", 60) == []


@pytest.fixture
async def redis() -> AsyncIterator[FakeAsyncRedis]:
    client = FakeAsyncRedis()
    yield client
    await client.aclose()


TEXT = "This is the first sentence of the reply. And here is the second one, a bit longer."


async def test_speak_synthesises_each_sentence_once(redis: FakeAsyncRedis) -> None:
    tts = FakeTTSProvider()
    speaker = Speaker(tts, redis, ttl_sec=60)

    first = await speaker.speak(TEXT, "en", "mp3")
    again = await speaker.speak(TEXT, "en", "ogg")

    assert [c.text for c in tts.calls] == speaker.split(TEXT)
    assert len(tts.calls) == 2
    assert audio.sniff(first.data) == "mp3"
    assert audio.sniff(again.data) == "ogg"
    assert first.mime == "audio/mpeg"
    key = speaker.key(speaker.split(TEXT)[0], "en")
    assert key.startswith("tts:v1:fake:fake-tts:test-voice:en:")
    assert 0 < await redis.ttl(key) <= 60


async def test_the_cache_is_per_voice_and_language(redis: FakeAsyncRedis) -> None:
    tts = FakeTTSProvider()
    await Speaker(tts, redis, ttl_sec=60).speak(TEXT, "en", "mp3")
    await Speaker(tts, redis, ttl_sec=60).speak(TEXT, "hi", "mp3")
    await Speaker(FakeTTSProvider(voice="other"), redis, ttl_sec=60).speak(TEXT, "en", "mp3")
    assert len(tts.calls) == 4


async def test_stream_yields_sentences_in_order(redis: FakeAsyncRedis) -> None:
    tts = FakeTTSProvider()
    speaker = Speaker(tts, redis, ttl_sec=60)

    chunks = [chunk async for chunk in speaker.stream(TEXT, "en", "wav")]

    assert len(chunks) == 2
    assert all(audio.sniff(c.data) == "wav" for c in chunks)
    durations = [audio.duration_sec(c.data) for c in chunks]
    assert durations[0] < durations[1]  # the fake's audio grows with the text


async def test_nothing_to_say_is_an_error(redis: FakeAsyncRedis) -> None:
    speaker = Speaker(FakeTTSProvider(), redis, ttl_sec=60)
    with pytest.raises(TTSError):
        await speaker.speak("  ", "en", "mp3")
    with pytest.raises(TTSError):
        [chunk async for chunk in speaker.stream("", "en", "mp3")]


class BrokenRedis:
    def __init__(self) -> None:
        self.calls = 0

    async def mget(self, *_: Any) -> list[bytes]:
        self.calls += 1
        raise RedisConnectionError("down")

    async def set(self, *_: Any, **__: Any) -> None:
        self.calls += 1
        raise RedisConnectionError("down")

    async def delete(self, *_: Any) -> None:
        self.calls += 1
        raise RedisConnectionError("down")


async def test_a_redis_outage_costs_latency_not_the_reply() -> None:
    redis = BrokenRedis()
    now = [0.0]
    speaker = Speaker(
        FakeTTSProvider(),
        cast(Redis, redis),
        ttl_sec=60,
        retry_after_sec=30,
        monotonic=lambda: now[0],
    )

    assert audio.sniff((await speaker.speak(TEXT, "en", "mp3")).data) == "mp3"
    assert redis.calls == 1  # Redis is skipped after the first failure...
    await speaker.speak(TEXT, "en", "mp3")
    await speaker.forget(TEXT, "en")
    assert redis.calls == 1
    now[0] = 31.0  # ...until `retry_after_sec` has passed
    await speaker.speak(TEXT, "en", "mp3")
    assert redis.calls == 2


async def test_without_redis_nothing_is_cached() -> None:
    tts = FakeTTSProvider()
    speaker = Speaker(tts, None, ttl_sec=60)
    await speaker.speak(TEXT, "en", "mp3")
    await speaker.speak(TEXT, "en", "mp3")
    await speaker.forget(TEXT, "en")
    assert len(tts.calls) == 4


async def test_fallback_audio_is_not_cached(redis: FakeAsyncRedis) -> None:
    primary = FakeTTSProvider(failures=[TTSError("down")] * 4)
    backup = FakeTTSProvider(voice="backup")
    speaker = Speaker(FallbackTTS([primary, backup]), redis, ttl_sec=60)

    await speaker.speak(TEXT, "en", "mp3")

    assert len(backup.calls) == 2
    assert await redis.keys("tts:*") == []  # the main voice is used again once it is back


async def test_transient_tts_errors_are_retried(redis: FakeAsyncRedis) -> None:
    tts = FakeTTSProvider(failures=[TTSTransientError("busy")])
    await Speaker(tts, redis, ttl_sec=60).speak("One sentence that is long enough.", "en", "mp3")
    assert len(tts.calls) == 2


async def test_forget_deletes_the_cached_sentences(redis: FakeAsyncRedis) -> None:
    tts = FakeTTSProvider()
    speaker = Speaker(tts, redis, ttl_sec=60)
    await speaker.speak(TEXT, "en", "mp3")

    await speaker.forget(TEXT, "en")

    assert await redis.keys("tts:*") == []
    await speaker.speak(TEXT, "en", "mp3")
    assert len(tts.calls) == 4


async def test_unreadable_provider_audio_is_a_tts_error(redis: FakeAsyncRedis) -> None:
    class Garbage(FakeTTSProvider):
        async def _synthesize(self, text: str, language: Any) -> bytes:
            return b"not audio"

    with pytest.raises(TTSError, match="could not be read"):
        await Speaker(Garbage(), redis, ttl_sec=60).speak(TEXT, "en", "mp3")
