"""Scripted `LLMProvider` for tests and offline runs. Never calls the network."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from sahayak.llm.base import (
    ChatMessage,
    LLMError,
    LLMProvider,
    LLMResponse,
    RetryPolicy,
    StructuredResponse,
    TokenUsage,
)

# A scripted reply: a string (for `complete`), a dict validated against the requested schema, a
# ready model instance, or an exception to raise. A callable computes the reply from the call.
Reply = str | dict[str, Any] | BaseModel | Exception


@dataclass(frozen=True)
class FakeCall:
    operation: str
    messages: Sequence[ChatMessage]
    system: str | None
    schema: type[BaseModel] | None


async def _no_sleep(_seconds: float) -> None:
    return None


class FakeLLMProvider(LLMProvider):
    name = "fake"

    def __init__(
        self,
        replies: Sequence[Reply] | Callable[[FakeCall], Reply] = (),
        *,
        retry: RetryPolicy | None = None,
    ) -> None:
        super().__init__(retry or RetryPolicy(sleep=_no_sleep))
        self._script = replies if callable(replies) else list(replies)
        self.calls: list[FakeCall] = []

    @property
    def model(self) -> str:
        return "fake-model"

    async def _complete(
        self, messages: Sequence[ChatMessage], *, system: str | None, max_tokens: int
    ) -> LLMResponse:
        reply = self._next(FakeCall("complete", messages, system, None))
        if not isinstance(reply, str):
            raise TypeError(f"complete() needs a str reply, got {type(reply).__name__}")
        return LLMResponse(text=reply, model=self.model, usage=_usage(messages, reply))

    async def _structured[T: BaseModel](
        self,
        messages: Sequence[ChatMessage],
        schema: type[T],
        *,
        system: str | None,
        max_tokens: int,
    ) -> StructuredResponse[T]:
        reply = self._next(FakeCall("structured", messages, system, schema))
        if isinstance(reply, str):
            raise TypeError("structured() needs a dict or model reply, got str")
        value = schema.model_validate(reply.model_dump() if isinstance(reply, BaseModel) else reply)
        return StructuredResponse(value=value, model=self.model, usage=_usage(messages, reply))

    def _next(self, call: FakeCall) -> str | dict[str, Any] | BaseModel:
        self.calls.append(call)
        if callable(self._script):
            reply = self._script(call)
        elif self._script:
            reply = self._script.pop(0)
        else:
            raise LLMError("FakeLLMProvider has no scripted reply left")
        if isinstance(reply, Exception):
            raise reply
        return reply


def _usage(messages: Sequence[ChatMessage], reply: object) -> TokenUsage:
    # Rough word counts, so code that records usage has non-zero numbers to work with.
    words_in = sum(len(m.content.split()) for m in messages)
    return TokenUsage(input_tokens=words_in, output_tokens=len(str(reply).split()))
