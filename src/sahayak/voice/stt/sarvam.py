"""`STTProvider` over Sarvam AI's speech-to-text REST API (`POST /speech-to-text`, D-041).

Language: Sarvam detects the language itself (`language_code=unknown`), which follows users who
switch between Hindi, Marathi and English. Detection is unreliable on very short answers ("हाँ"
comes back as "Yeah." in English), and a language hint makes Sarvam write in that language even
when the speaker used another. So: detect first; if the result disagrees with the conversation's
language and the answer is short or the detection unsure, transcribe again with the
conversation's language as the hint (D-043).

Sarvam reports no confidence for the words, only for the detected language, so `confidence` is
None (D-043).
"""

from typing import Any

import httpx

from sahayak.eligibility.profile import Language
from sahayak.llm.base import RetryPolicy
from sahayak.voice.stt.base import (
    STTAudioError,
    STTError,
    STTProvider,
    STTTransientError,
    Transcript,
)

API_URL = "https://api.sarvam.ai/speech-to-text"
LANGUAGE_CODES: dict[Language, str] = {"hi": "hi-IN", "mr": "mr-IN", "en": "en-IN"}
_LANGUAGES: dict[str, Language] = {code: language for language, code in LANGUAGE_CODES.items()}

# Re-check a detected language that differs from the conversation's when the answer has at most
# this many words, or when Sarvam's language probability is below the threshold.
SHORT_ANSWER_WORDS = 3
RECHECK_BELOW_PROBABILITY = 0.9


class SarvamSTT(STTProvider):
    name = "sarvam"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_sec: float,
        retry: RetryPolicy | None = None,
        http_client: httpx.AsyncClient | None = None,
        api_url: str = API_URL,
    ) -> None:
        super().__init__(retry)
        self._api_key = api_key
        self._model = model
        self._url = api_url
        self._client = http_client or httpx.AsyncClient(timeout=timeout_sec)

    @property
    def model(self) -> str:
        return self._model

    async def _transcribe(
        self, audio: bytes, *, mime: str, language_hint: Language | None
    ) -> Transcript:
        body = await self._request(audio, mime, "unknown")
        transcript = self._transcript(body)
        if language_hint is None or transcript.empty or transcript.language == language_hint:
            return transcript
        probability = body.get("language_probability")
        short = len(transcript.text.split()) <= SHORT_ANSWER_WORDS
        unsure = not isinstance(probability, int | float) or probability < RECHECK_BELOW_PROBABILITY
        if not (short or unsure):
            return transcript  # a clear sentence in another language: the user switched
        return self._transcript(
            await self._request(audio, mime, LANGUAGE_CODES[language_hint]), language_hint
        )

    async def _request(self, audio: bytes, mime: str, language_code: str) -> dict[str, Any]:
        try:
            response = await self._client.post(
                self._url,
                headers={"api-subscription-key": self._api_key},
                files={"file": ("audio", audio, mime)},
                data={"model": self._model, "language_code": language_code},
            )
        except httpx.TimeoutException as exc:
            raise STTTransientError("sarvam timed out") from exc
        except httpx.TransportError as exc:
            raise STTTransientError(f"sarvam connection error: {type(exc).__name__}") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise STTTransientError(f"sarvam HTTP {response.status_code}")
        if response.status_code in (400, 422):
            raise STTAudioError(f"sarvam rejected the audio (HTTP {response.status_code})")
        if response.status_code != 200:
            raise STTError(f"sarvam HTTP {response.status_code}")
        try:
            body: dict[str, Any] = response.json()
        except ValueError as exc:
            raise STTError("sarvam returned a body that is not JSON") from exc
        if not isinstance(body.get("transcript"), str):
            raise STTError("sarvam returned no transcript")
        return body

    def _transcript(self, body: dict[str, Any], language: Language | None = None) -> Transcript:
        detected = _LANGUAGES.get(str(body.get("language_code")))
        return Transcript(
            text=" ".join(str(body["transcript"]).split()),
            language=detected or language,
            confidence=None,
            provider=self.name,
            model=self._model,
        )
