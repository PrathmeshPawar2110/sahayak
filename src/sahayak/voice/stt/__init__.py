"""Speech-to-text providers. Use `create_stt_provider`; vendor SDKs load only when chosen."""

from typing import Literal

import structlog

from sahayak.config import Settings
from sahayak.voice.stt.base import (
    FallbackSTT,
    STTAudioError,
    STTError,
    STTProvider,
    STTTransientError,
    Transcript,
)

log = structlog.get_logger(__name__)

__all__ = [
    "STTAudioError",
    "STTConfigError",
    "STTError",
    "STTProvider",
    "STTTransientError",
    "Transcript",
    "create_stt_provider",
]


class STTConfigError(STTError):
    """Required settings for the selected provider are missing."""


def _provider(
    settings: Settings, name: Literal["sarvam", "whisper", "indicconformer"]
) -> STTProvider:
    if name == "sarvam":
        if not settings.sarvam_api_key.get_secret_value():
            raise STTConfigError("STT provider sarvam needs SARVAM_API_KEY")
        if not settings.sarvam_stt_model:
            raise STTConfigError("STT provider sarvam needs SARVAM_STT_MODEL")
        from sahayak.voice.stt.sarvam import SarvamSTT

        return SarvamSTT(
            api_key=settings.sarvam_api_key.get_secret_value(),
            model=settings.sarvam_stt_model,
            timeout_sec=settings.speech_timeout_sec,
        )
    if name == "whisper":
        if not settings.openai_api_key.get_secret_value():
            raise STTConfigError("STT provider whisper needs OPENAI_API_KEY")
        if not settings.openai_stt_model:
            raise STTConfigError("STT provider whisper needs OPENAI_STT_MODEL")
        from sahayak.voice.stt.whisper import WhisperSTT

        return WhisperSTT(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.openai_stt_model,
            timeout_sec=settings.speech_timeout_sec,
        )
    raise STTConfigError(f"STT provider {name} is not implemented yet")


def create_stt_provider(settings: Settings) -> STTProvider:
    """The configured provider, followed by the fallback when one is configured."""
    primary = _provider(settings, settings.stt_provider)
    fallback = settings.stt_fallback_provider
    if not fallback or fallback == settings.stt_provider:
        return primary
    try:
        second = _provider(settings, fallback)
    except STTConfigError as exc:
        log.warning("stt_fallback_disabled", reason=str(exc))
        return primary
    return FallbackSTT([primary, second])
