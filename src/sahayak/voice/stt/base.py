"""Provider-agnostic speech-to-text (TRD §7, FR-1-FR-3).

Implementations translate one request into one vendor call and map vendor errors onto the
exception types below. `STTProvider.transcribe` adds what every provider shares: retries, a
tracing span and a log line with timings but never the transcript. `FallbackSTT` tries a second
provider when the first fails (NFR Reliability).
"""

import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

import structlog
from opentelemetry import trace

from sahayak.eligibility.profile import Language
from sahayak.llm.base import RetryPolicy

log = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)

# Speech is interactive: retry once, quickly, then fall back to the other provider.
SPEECH_RETRY = RetryPolicy(attempts=2, base_delay_sec=0.25, max_delay_sec=1.0)


@dataclass(frozen=True)
class Transcript:
    text: str
    # hi/mr/en as detected, or None when the provider did not say (or heard another language).
    language: Language | None
    # The provider's confidence in the words, 0-1, or None if it gives none (see D-043).
    confidence: float | None
    provider: str
    model: str

    @property
    def empty(self) -> bool:
        return not self.text.strip()


class STTError(Exception):
    """Transcription failed and retrying the same request will not help."""


class STTTransientError(STTError):
    """Rate limit, timeout, connection error or 5xx: safe to retry."""


class STTAudioError(STTError):
    """The provider could not use the audio (unsupported format, too long, corrupt)."""


class STTProvider(ABC):
    name: str

    def __init__(self, retry: RetryPolicy | None = None) -> None:
        self._retry = retry or SPEECH_RETRY

    @property
    @abstractmethod
    def model(self) -> str: ...

    async def aclose(self) -> None:
        """Close the provider's HTTP client (providers without one have nothing to do)."""
        return None

    async def transcribe(
        self, audio: bytes, *, mime: str, language_hint: Language | None = None
    ) -> Transcript:
        """Transcribe one utterance. `language_hint` is the conversation's language, if known."""
        with tracer.start_as_current_span("voice.stt") as span:
            span.set_attribute("stt.provider", self.name)
            span.set_attribute("stt.model", self.model)
            span.set_attribute("stt.audio_bytes", len(audio))
            for attempt in range(1, self._retry.attempts + 1):
                started = time.perf_counter()
                try:
                    transcript = await self._transcribe(
                        audio, mime=mime, language_hint=language_hint
                    )
                except STTTransientError as exc:
                    if attempt == self._retry.attempts:
                        log.warning(
                            "stt_call_failed", provider=self.name, attempts=attempt, error=str(exc)
                        )
                        raise
                    await self._retry.sleep(self._retry.delay(attempt))
                    continue
                latency_ms = round((time.perf_counter() - started) * 1000)
                span.set_attribute("stt.latency_ms", latency_ms)
                span.set_attribute("stt.attempts", attempt)
                if transcript.confidence is not None:
                    span.set_attribute("stt.confidence", transcript.confidence)
                log.info(
                    "stt_call",
                    provider=self.name,
                    model=self.model,
                    attempts=attempt,
                    latency_ms=latency_ms,
                    audio_bytes=len(audio),
                    language=transcript.language,
                    confidence=transcript.confidence,
                    chars=len(transcript.text),
                )
                return transcript
        raise AssertionError("unreachable: the loop returns or raises")  # pragma: no cover

    @abstractmethod
    async def _transcribe(
        self, audio: bytes, *, mime: str, language_hint: Language | None
    ) -> Transcript: ...


class FallbackSTT(STTProvider):
    """Try each provider in order until one succeeds."""

    def __init__(self, providers: Sequence[STTProvider]) -> None:
        super().__init__(RetryPolicy(attempts=1))
        if not providers:
            raise ValueError("FallbackSTT needs at least one provider")
        self._providers = list(providers)
        self.name = "+".join(p.name for p in self._providers)

    @property
    def model(self) -> str:
        return self._providers[0].model

    async def aclose(self) -> None:
        for provider in self._providers:
            await provider.aclose()

    async def transcribe(
        self, audio: bytes, *, mime: str, language_hint: Language | None = None
    ) -> Transcript:
        last: STTError | None = None
        for provider in self._providers:
            try:
                return await provider.transcribe(audio, mime=mime, language_hint=language_hint)
            except STTError as exc:
                log.warning("stt_fallback", failed=provider.name, error=type(exc).__name__)
                last = exc
        if last is None:  # pragma: no cover - there is always at least one provider
            raise AssertionError("no provider ran")
        raise last

    async def _transcribe(
        self, audio: bytes, *, mime: str, language_hint: Language | None
    ) -> Transcript:  # pragma: no cover - `transcribe` is overridden
        raise NotImplementedError
