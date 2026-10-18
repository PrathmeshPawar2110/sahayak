"""Text-to-speech providers. Use `create_tts_provider`; vendor SDKs load only when chosen."""

from typing import Literal

import structlog

from sahayak.config import Settings
from sahayak.voice.tts.base import (
    FallbackTTS,
    Synthesis,
    TTSError,
    TTSProvider,
    TTSTransientError,
)

log = structlog.get_logger(__name__)

__all__ = [
    "Synthesis",
    "TTSConfigError",
    "TTSError",
    "TTSProvider",
    "TTSTransientError",
    "create_tts_provider",
]


class TTSConfigError(TTSError):
    """Required settings for the selected provider are missing."""


def _provider(settings: Settings, name: Literal["sarvam", "openai", "indic_parler"]) -> TTSProvider:
    if name == "sarvam":
        if not settings.sarvam_api_key.get_secret_value():
            raise TTSConfigError("TTS provider sarvam needs SARVAM_API_KEY")
        if not settings.sarvam_tts_model:
            raise TTSConfigError("TTS provider sarvam needs SARVAM_TTS_MODEL")
        from sahayak.voice.tts.sarvam import SarvamTTS

        return SarvamTTS(
            api_key=settings.sarvam_api_key.get_secret_value(),
            model=settings.sarvam_tts_model,
            voice=settings.sarvam_tts_voice,
            timeout_sec=settings.speech_timeout_sec,
        )
    if name == "openai":
        if not settings.openai_api_key.get_secret_value():
            raise TTSConfigError("TTS provider openai needs OPENAI_API_KEY")
        if not (settings.openai_tts_model and settings.openai_tts_voice):
            raise TTSConfigError("TTS provider openai needs OPENAI_TTS_MODEL and OPENAI_TTS_VOICE")
        from sahayak.voice.tts.openai import OpenAITTS

        return OpenAITTS(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.openai_tts_model,
            voice=settings.openai_tts_voice,
            timeout_sec=settings.speech_timeout_sec,
        )
    raise TTSConfigError(f"TTS provider {name} is not implemented yet")


def create_tts_provider(settings: Settings) -> TTSProvider:
    """The configured provider, followed by the fallback when one is configured."""
    primary = _provider(settings, settings.tts_provider)
    fallback = settings.tts_fallback_provider
    if not fallback or fallback == settings.tts_provider:
        return primary
    try:
        second = _provider(settings, fallback)
    except TTSConfigError as exc:
        log.warning("tts_fallback_disabled", reason=str(exc))
        return primary
    return FallbackTTS([primary, second])
