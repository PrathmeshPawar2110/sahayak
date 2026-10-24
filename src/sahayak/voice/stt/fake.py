"""Scripted `STTProvider` for tests and offline runs. Never calls the network."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sahayak.eligibility.profile import Language
from sahayak.llm.base import RetryPolicy
from sahayak.voice.stt.base import STTProvider, Transcript

# A scripted reply: the transcript text, a ready transcript, or an exception to raise. A callable
# computes it from the call.
STTReply = str | Transcript | Exception


@dataclass(frozen=True)
class STTCall:
    audio: bytes
    mime: str
    language_hint: Language | None


async def _no_sleep(_seconds: float) -> None:
    return None


class FakeSTTProvider(STTProvider):
    name = "fake"

    def __init__(
        self,
        replies: Sequence[STTReply] | Callable[[STTCall], STTReply] = (),
        *,
        retry: RetryPolicy | None = None,
    ) -> None:
        super().__init__(retry or RetryPolicy(attempts=2, sleep=_no_sleep))
        self._script = replies if callable(replies) else list(replies)
        self.calls: list[STTCall] = []

    @property
    def model(self) -> str:
        return "fake-stt"

    async def _transcribe(
        self, audio: bytes, *, mime: str, language_hint: Language | None
    ) -> Transcript:
        call = STTCall(audio, mime, language_hint)
        self.calls.append(call)
        if callable(self._script):
            reply = self._script(call)
        elif self._script:
            reply = self._script.pop(0)
        else:
            raise AssertionError("FakeSTTProvider ran out of scripted replies")
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, Transcript):
            return reply
        return Transcript(
            text=reply,
            language=language_hint,
            confidence=None,
            provider=self.name,
            model=self.model,
        )
