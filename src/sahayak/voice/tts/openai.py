"""`TTSProvider` over OpenAI's speech API: the fallback (`TTS_FALLBACK_PROVIDER=openai`, D-041).

OpenAI's voices speak Hindi and Marathi with an accent; they are a fallback, not the default.
"""

import openai

from sahayak.eligibility.profile import Language
from sahayak.llm.base import RetryPolicy
from sahayak.voice.tts.base import TTSError, TTSProvider, TTSTransientError

_RETRYABLE_STATUS = frozenset({408, 409, 429})


class OpenAITTS(TTSProvider):
    name = "openai"
    max_chars = 4096

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        voice: str,
        timeout_sec: float,
        retry: RetryPolicy | None = None,
        client: openai.AsyncOpenAI | None = None,
    ) -> None:
        super().__init__(retry)
        self._model = model
        self._voice = voice
        # SDK retries are off: the base class owns the retry policy.
        self._client = client or openai.AsyncOpenAI(
            api_key=api_key, timeout=timeout_sec, max_retries=0
        )

    @property
    def model(self) -> str:
        return self._model

    async def aclose(self) -> None:
        await self._client.close()

    @property
    def voice(self) -> str:
        return self._voice

    async def _synthesize(self, text: str, language: Language) -> bytes:
        try:
            response = await self._client.audio.speech.create(
                model=self._model, voice=self._voice, input=text, response_format="wav"
            )
        except openai.APIConnectionError as exc:  # includes timeouts
            raise TTSTransientError(f"openai connection error: {type(exc).__name__}") from exc
        except openai.APIStatusError as exc:
            message = f"openai HTTP {exc.status_code}"
            if exc.status_code in _RETRYABLE_STATUS or exc.status_code >= 500:
                raise TTSTransientError(message) from exc
            raise TTSError(message) from exc
        except openai.APIError as exc:
            raise TTSError(f"openai error: {type(exc).__name__}") from exc
        audio = response.content
        if not audio:
            raise TTSError("openai returned no audio")
        return audio
