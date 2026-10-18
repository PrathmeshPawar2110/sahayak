"""Provider-agnostic text-to-speech (TRD §7, FR-10).

A provider turns one piece of text (at most `max_chars`) into audio in one request. It may
return any container FFmpeg reads (WAV in practice); `sahayak.voice.speech.Speaker` splits
replies into sentences, caches them and converts them to the format a channel needs.

`TTSProvider.synthesize` adds retries, a tracing span and a log line with timings but never
the text. `FallbackTTS` tries a second provider when the first fails (NFR Reliability).
"""

import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

import structlog
from opentelemetry import trace

from sahayak.eligibility.profile import Language
from sahayak.llm.base import RetryPolicy
from sahayak.voice.stt.base import SPEECH_RETRY

log = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


@dataclass(frozen=True)
class Synthesis:
    audio: bytes
    voice_id: str  # which provider, model and voice produced it (`TTSProvider.voice_id`)


class TTSError(Exception):
    """Synthesis failed and retrying the same request will not help."""


class TTSTransientError(TTSError):
    """Rate limit, timeout, connection error or 5xx: safe to retry."""


class TTSProvider(ABC):
    name: str
    max_chars: int = 1000

    def __init__(self, retry: RetryPolicy | None = None) -> None:
        self._retry = retry or SPEECH_RETRY

    @property
    @abstractmethod
    def model(self) -> str: ...

    async def aclose(self) -> None:
        """Close the provider's HTTP client (providers without one have nothing to do)."""
        return None

    @property
    @abstractmethod
    def voice(self) -> str: ...

    @property
    def voice_id(self) -> str:
        """Identifies how audio sounds: cached audio is reused only for the same voice."""
        return f"{self.name}:{self.model}:{self.voice}"

    async def synthesize(self, text: str, language: Language) -> Synthesis:
        if len(text) > self.max_chars:
            raise TTSError(f"{self.name} takes at most {self.max_chars} characters per request")
        with tracer.start_as_current_span("voice.tts") as span:
            span.set_attribute("tts.provider", self.name)
            span.set_attribute("tts.model", self.model)
            span.set_attribute("tts.chars", len(text))
            for attempt in range(1, self._retry.attempts + 1):
                started = time.perf_counter()
                try:
                    audio = await self._synthesize(text, language)
                except TTSTransientError as exc:
                    if attempt == self._retry.attempts:
                        log.warning(
                            "tts_call_failed", provider=self.name, attempts=attempt, error=str(exc)
                        )
                        raise
                    await self._retry.sleep(self._retry.delay(attempt))
                    continue
                latency_ms = round((time.perf_counter() - started) * 1000)
                span.set_attribute("tts.latency_ms", latency_ms)
                span.set_attribute("tts.attempts", attempt)
                log.info(
                    "tts_call",
                    provider=self.name,
                    model=self.model,
                    language=language,
                    attempts=attempt,
                    latency_ms=latency_ms,
                    chars=len(text),
                    audio_bytes=len(audio),
                )
                return Synthesis(audio, self.voice_id)
        raise AssertionError("unreachable: the loop returns or raises")  # pragma: no cover

    @abstractmethod
    async def _synthesize(self, text: str, language: Language) -> bytes: ...


class FallbackTTS(TTSProvider):
    """Try each provider in order until one succeeds. Its voice is the first provider's."""

    def __init__(self, providers: Sequence[TTSProvider]) -> None:
        super().__init__(RetryPolicy(attempts=1))
        if not providers:
            raise ValueError("FallbackTTS needs at least one provider")
        self._providers = list(providers)
        self.name = self._providers[0].name
        self.max_chars = min(p.max_chars for p in self._providers)

    @property
    def model(self) -> str:
        return self._providers[0].model

    async def aclose(self) -> None:
        for provider in self._providers:
            await provider.aclose()

    @property
    def voice(self) -> str:
        return self._providers[0].voice

    async def synthesize(self, text: str, language: Language) -> Synthesis:
        last: TTSError | None = None
        for provider in self._providers:
            try:
                return await provider.synthesize(text, language)
            except TTSError as exc:
                log.warning("tts_fallback", failed=provider.name, error=type(exc).__name__)
                last = exc
        if last is None:  # pragma: no cover - there is always at least one provider
            raise AssertionError("no provider ran")
        raise last

    async def _synthesize(
        self, text: str, language: Language
    ) -> bytes:  # pragma: no cover - `synthesize` is overridden
        raise NotImplementedError
