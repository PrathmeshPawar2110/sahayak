"""`TTSProvider` for tests and offline runs: silent WAV whose length follows the text."""

from collections.abc import Sequence
from dataclasses import dataclass

from sahayak.eligibility.profile import Language
from sahayak.llm.base import RetryPolicy
from sahayak.voice.audio import pcm_to_wav
from sahayak.voice.tts.base import TTSProvider

SAMPLE_RATE = 16_000
SECONDS_PER_CHAR = 0.01


@dataclass(frozen=True)
class TTSCall:
    text: str
    language: Language


async def _no_sleep(_seconds: float) -> None:
    return None


class FakeTTSProvider(TTSProvider):
    name = "fake"

    def __init__(
        self,
        *,
        failures: Sequence[Exception] = (),
        voice: str = "test-voice",
        max_chars: int = 1000,
        retry: RetryPolicy | None = None,
    ) -> None:
        super().__init__(retry or RetryPolicy(attempts=2, sleep=_no_sleep))
        self._failures = list(failures)
        self._voice = voice
        self.max_chars = max_chars
        self.calls: list[TTSCall] = []

    @property
    def model(self) -> str:
        return "fake-tts"

    @property
    def voice(self) -> str:
        return self._voice

    async def _synthesize(self, text: str, language: Language) -> bytes:
        self.calls.append(TTSCall(text, language))
        if self._failures:
            raise self._failures.pop(0)
        samples = max(1, round(len(text) * SECONDS_PER_CHAR * SAMPLE_RATE))
        return pcm_to_wav(b"\0\0" * samples, SAMPLE_RATE)
