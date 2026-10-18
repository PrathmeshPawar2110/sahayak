"""`STTProvider` over OpenAI's transcription API: the fallback (`STT_PROVIDER=whisper`, D-041).

The default model is `gpt-4o-mini-transcribe`. Its token log-probabilities give a confidence:
the geometric mean of the token probabilities. `whisper-1` is also supported; its confidence is
the same mean over segments' average log-probabilities.

OpenAI's models need the language hint: without it `whisper-1` writes Hindi in Urdu script. A
hint is passed whenever one is known.
"""

import math
from collections.abc import Iterable

import openai
from openai.types.audio import Transcription

from sahayak.eligibility.profile import Language
from sahayak.llm.base import RetryPolicy
from sahayak.voice.stt.base import (
    STTAudioError,
    STTError,
    STTProvider,
    STTTransientError,
    Transcript,
)

_RETRYABLE_STATUS = frozenset({408, 409, 429})
# Language names `whisper-1` reports.
_LANGUAGE_NAMES: dict[str, Language] = {"hindi": "hi", "marathi": "mr", "english": "en"}


class WhisperSTT(STTProvider):
    name = "whisper"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_sec: float,
        retry: RetryPolicy | None = None,
        client: openai.AsyncOpenAI | None = None,
    ) -> None:
        super().__init__(retry)
        self._model = model
        # SDK retries are off: the base class owns the retry policy.
        self._client = client or openai.AsyncOpenAI(
            api_key=api_key, timeout=timeout_sec, max_retries=0
        )

    @property
    def model(self) -> str:
        return self._model

    async def _transcribe(
        self, audio: bytes, *, mime: str, language_hint: Language | None
    ) -> Transcript:
        file = ("audio", audio, mime)
        language = language_hint or openai.omit
        try:
            if self._model.startswith("whisper"):
                verbose = await self._client.audio.transcriptions.create(
                    file=file, model=self._model, language=language, response_format="verbose_json"
                )
                detected = _LANGUAGE_NAMES.get(verbose.language.casefold())
                text = verbose.text
                confidence = _mean_probability(s.avg_logprob for s in verbose.segments or [])
            else:
                result = await self._client.audio.transcriptions.create(
                    file=file,
                    model=self._model,
                    language=language,
                    response_format="json",
                    include=["logprobs"],
                )
                if not isinstance(result, Transcription):
                    raise STTError(f"whisper returned {type(result).__name__}")
                detected = None
                text = result.text
                confidence = _mean_probability(
                    t.logprob for t in result.logprobs or [] if t.logprob is not None
                )
        except openai.APIConnectionError as exc:  # includes timeouts
            raise STTTransientError(f"whisper connection error: {type(exc).__name__}") from exc
        except openai.APIStatusError as exc:
            message = f"whisper HTTP {exc.status_code}"
            if exc.status_code in _RETRYABLE_STATUS or exc.status_code >= 500:
                raise STTTransientError(message) from exc
            if exc.status_code == 400:
                raise STTAudioError(message) from exc
            raise STTError(message) from exc
        except openai.APIError as exc:
            raise STTError(f"whisper error: {type(exc).__name__}") from exc
        return Transcript(
            text=" ".join(text.split()),
            language=detected or language_hint,
            confidence=confidence,
            provider=self.name,
            model=self._model,
        )


def _mean_probability(logprobs: Iterable[float]) -> float | None:
    values = list(logprobs)
    if not values:
        return None
    return round(math.exp(sum(values) / len(values)), 4)
