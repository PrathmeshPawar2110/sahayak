"""The voice pipeline's services and its per-stage timings (TRD §6, §12.5, M5-T09).

A voice turn is STT -> agent -> TTS. `VoiceServices` holds the configured speech-to-text
provider and the `Speaker`; either can be missing (no API key), and then the API answers voice
requests with 503 and text turns without audio. `StageTimer` measures each stage, and
`log_voice_turn` writes one `voice_turn` event per turn with the timings, never the words:
`sahayak latency` reads those events back for p50/p95.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal

import structlog
from redis.asyncio import Redis

from sahayak.config import Settings
from sahayak.voice.speech import Speaker
from sahayak.voice.stt import STTError, STTProvider, create_stt_provider
from sahayak.voice.tts import TTSError, create_tts_provider

log = structlog.get_logger(__name__)

Stage = Literal["stt", "agent", "tts"]
STAGES: tuple[Stage, ...] = ("stt", "agent", "tts")


@dataclass(frozen=True)
class VoiceServices:
    stt: STTProvider | None = None
    speaker: Speaker | None = None
    max_upload_bytes: int = 5 * 1024 * 1024
    partial_interval_sec: float = 2.0

    async def aclose(self) -> None:
        if self.stt is not None:
            await self.stt.aclose()
        if self.speaker is not None:
            await self.speaker.aclose()


def voice_services(settings: Settings, redis: Redis | None) -> VoiceServices:
    """Speech providers from settings. A provider that is not configured is left out."""
    try:
        stt: STTProvider | None = create_stt_provider(settings)
    except STTError as exc:
        log.warning("voice_stt_disabled", reason=str(exc))
        stt = None
    try:
        tts = create_tts_provider(settings)
    except TTSError as exc:
        log.warning("voice_tts_disabled", reason=str(exc))
        speaker = None
    else:
        speaker = Speaker(tts, redis, ttl_sec=settings.tts_cache_ttl_days * 86_400)
    return VoiceServices(
        stt=stt,
        speaker=speaker,
        max_upload_bytes=round(settings.max_voice_upload_mb * 1024 * 1024),
        partial_interval_sec=settings.stream_partial_interval_sec,
    )


@dataclass
class StageTimer:
    """Milliseconds per stage of one voice turn, from when the user finished speaking."""

    started: float = field(default_factory=time.perf_counter)
    ms: dict[Stage, int] = field(default_factory=dict)

    @contextmanager
    def stage(self, name: Stage) -> Iterator[None]:
        begun = time.perf_counter()
        try:
            yield
        finally:
            self.ms[name] = round((time.perf_counter() - begun) * 1000)

    def mark(self, name: Stage, since: float) -> None:
        """Record a stage that ended now and began at `since` (a `perf_counter` value)."""
        self.ms[name] = round((time.perf_counter() - since) * 1000)

    def total_ms(self) -> int:
        return round((time.perf_counter() - self.started) * 1000)


Outcome = Literal["answered", "no_speech", "stt_failed"]
TTSStatus = Literal["ok", "failed", "skipped"]


def log_voice_turn(
    timer: StageTimer,
    *,
    channel: Literal["upload", "stream"],
    outcome: Outcome,
    language: str | None,
    stt_provider: str | None,
    asr_confidence: float | None,
    transcript_chars: int,
    tts: TTSStatus,
    total_ms: int | None = None,
) -> None:
    """One event per voice turn. `tts_ms` is the time until the reply's audio was ready: the
    whole file for uploads, the first sentence for streams (TRD §5: until the first audio)."""
    log.info(
        "voice_turn",
        channel=channel,
        outcome=outcome,
        language=language,
        stt_provider=stt_provider,
        asr_confidence=asr_confidence,
        transcript_chars=transcript_chars,
        tts=tts,
        stt_ms=timer.ms.get("stt"),
        agent_ms=timer.ms.get("agent"),
        tts_ms=timer.ms.get("tts"),
        total_ms=timer.total_ms() if total_ms is None else total_ms,
    )
