"""`TTSProvider` over Sarvam AI's text-to-speech REST API (`POST /text-to-speech`, D-041).

Sarvam returns base64 WAV. Its newer models reject Opus output, so OGG/Opus for WhatsApp is
made from the WAV (`sahayak.voice.audio`).
"""

import base64
import binascii
from typing import Any

import httpx

from sahayak.eligibility.profile import Language
from sahayak.llm.base import RetryPolicy
from sahayak.voice.audio import SAMPLE_RATE
from sahayak.voice.stt.sarvam import LANGUAGE_CODES
from sahayak.voice.tts.base import TTSError, TTSProvider, TTSTransientError

API_URL = "https://api.sarvam.ai/text-to-speech"


class SarvamTTS(TTSProvider):
    name = "sarvam"
    # bulbul:v2 takes 1500 characters, v3 2500: replies are sent a sentence or two at a time.
    max_chars = 1500

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        voice: str,
        timeout_sec: float,
        retry: RetryPolicy | None = None,
        http_client: httpx.AsyncClient | None = None,
        api_url: str = API_URL,
    ) -> None:
        super().__init__(retry)
        self._api_key = api_key
        self._model = model
        self._voice = voice
        self._url = api_url
        self._client = http_client or httpx.AsyncClient(timeout=timeout_sec)

    @property
    def model(self) -> str:
        return self._model

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def voice(self) -> str:
        return self._voice

    async def _synthesize(self, text: str, language: Language) -> bytes:
        payload: dict[str, Any] = {
            "text": text,
            "language_code": LANGUAGE_CODES[language],
            "model": self._model,
            "speech_sample_rate": SAMPLE_RATE,
            "output_audio_codec": "wav",
        }
        if self._voice:
            payload["speaker"] = self._voice
        try:
            response = await self._client.post(
                self._url, headers={"api-subscription-key": self._api_key}, json=payload
            )
        except httpx.TimeoutException as exc:
            raise TTSTransientError("sarvam timed out") from exc
        except httpx.TransportError as exc:
            raise TTSTransientError(f"sarvam connection error: {type(exc).__name__}") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise TTSTransientError(f"sarvam HTTP {response.status_code}")
        if response.status_code != 200:
            raise TTSError(f"sarvam HTTP {response.status_code}")
        try:
            audios = response.json()["audios"]
            return base64.b64decode(audios[0], validate=True)
        except (ValueError, KeyError, IndexError, TypeError, binascii.Error) as exc:
            raise TTSError("sarvam returned no audio") from exc
