"""Calls the speech providers for real: speak a sentence, transcribe it back. Run with
`uv run pytest -m live` (costs money; needs SARVAM_API_KEY, and OPENAI_API_KEY for OpenAI)."""

from typing import Literal

import pytest

from sahayak.config import Settings
from sahayak.eligibility.profile import Language
from sahayak.evals.asr import edit_distance, lenient
from sahayak.voice import audio
from sahayak.voice.speech import Speaker
from sahayak.voice.stt import STTConfigError, create_stt_provider
from sahayak.voice.tts import TTSConfigError, create_tts_provider

pytestmark = pytest.mark.live

SENTENCES: dict[Language, str] = {
    "hi": "मेरे पास दो एकड़ ज़मीन है, मुझे कौन सी योजना मिलेगी?",
    "mr": "माझ्याकडे दोन एकर शेती आहे, मला कोणत्या योजना मिळतील?",
    "en": "I am a farmer in Maharashtra with two acres of land.",
}


@pytest.mark.parametrize("provider", ["sarvam", "openai"])
@pytest.mark.parametrize("language", ["hi", "mr", "en"])
async def test_round_trip(provider: Literal["sarvam", "openai"], language: Language) -> None:
    stt_provider: Literal["sarvam", "whisper"] = "sarvam" if provider == "sarvam" else "whisper"
    settings = Settings().model_copy(
        update={
            "tts_provider": provider,
            "stt_provider": stt_provider,
            "tts_fallback_provider": "",
            "stt_fallback_provider": "",
        }
    )
    try:
        speaker = Speaker(create_tts_provider(settings), None, ttl_sec=1)
        stt = create_stt_provider(settings)
    except (STTConfigError, TTSConfigError) as exc:
        pytest.skip(str(exc))

    try:
        spoken = await speaker.speak(SENTENCES[language], language, "ogg")
        assert audio.sniff(spoken.data) == "ogg"
        transcript = await stt.transcribe(spoken.data, mime=spoken.mime, language_hint=language)
    finally:
        await speaker.aclose()
        await stt.aclose()

    reference = lenient(SENTENCES[language]).split()
    errors = edit_distance(reference, lenient(transcript.text).split())
    assert errors / len(reference) <= 0.35, transcript.text
    assert transcript.language in (language, None)


async def test_sarvam_detects_the_language_of_a_first_turn() -> None:
    settings = Settings().model_copy(update={"stt_fallback_provider": ""})
    try:
        speaker = Speaker(create_tts_provider(settings), None, ttl_sec=1)
        stt = create_stt_provider(settings)
    except (STTConfigError, TTSConfigError) as exc:
        pytest.skip(str(exc))
    try:
        spoken = await speaker.speak(SENTENCES["mr"], "mr", "mp3")
        transcript = await stt.transcribe(spoken.data, mime=spoken.mime)
    finally:
        await speaker.aclose()
        await stt.aclose()
    assert transcript.language == "mr"
