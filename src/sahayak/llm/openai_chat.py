"""`LLMProvider` over the OpenAI chat-completions API: OpenAI itself, and Azure OpenAI.

Both speak the same API through the `openai` SDK; they differ only in how the client is built
(`OpenAIProvider` here, `AzureOpenAIProvider` in `azure_openai.py`).
"""

from collections.abc import Sequence
from typing import Any

import httpx2
import openai
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, ValidationError

from sahayak.llm.base import (
    ChatMessage,
    LLMError,
    LLMOutputError,
    LLMProvider,
    LLMRefusalError,
    LLMResponse,
    LLMTransientError,
    RetryPolicy,
    StructuredResponse,
    TokenUsage,
)

_RETRYABLE_STATUS = frozenset({408, 409, 429})


class OpenAIChatProvider(LLMProvider):
    """Chat completions with structured outputs. Subclasses build the client."""

    def __init__(self, client: openai.AsyncOpenAI, model: str, retry: RetryPolicy | None) -> None:
        super().__init__(retry)
        self._client = client
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    async def _complete(
        self, messages: Sequence[ChatMessage], *, system: str | None, max_tokens: int
    ) -> LLMResponse:
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=_messages(messages, system),
                max_completion_tokens=max_tokens,
            )
        except openai.APIError as exc:
            raise self._translate(exc) from exc
        choice = response.choices[0]
        if choice.finish_reason == "content_filter":
            raise LLMRefusalError("the content filter blocked the response")
        if choice.finish_reason == "length":
            raise LLMOutputError("the response hit max_tokens and was cut off")
        return LLMResponse(
            text=choice.message.content or "",
            model=response.model,
            usage=_usage(response.usage),
        )

    async def _structured[T: BaseModel](
        self,
        messages: Sequence[ChatMessage],
        schema: type[T],
        *,
        system: str | None,
        max_tokens: int,
    ) -> StructuredResponse[T]:
        try:
            response = await self._client.chat.completions.parse(
                model=self._model,
                messages=_messages(messages, system),
                response_format=schema,
                max_completion_tokens=max_tokens,
            )
        except openai.LengthFinishReasonError as exc:
            raise LLMOutputError("the response hit max_tokens and was cut off") from exc
        except openai.ContentFilterFinishReasonError as exc:
            raise LLMRefusalError("the content filter blocked the response") from exc
        except ValidationError as exc:
            raise LLMOutputError("the response did not match the schema") from exc
        except openai.APIError as exc:
            raise self._translate(exc) from exc
        message = response.choices[0].message
        if message.refusal:
            raise LLMRefusalError("the model declined the request")
        if message.parsed is None:
            raise LLMOutputError("response did not contain the structured output")
        return StructuredResponse(
            value=message.parsed, model=response.model, usage=_usage(response.usage)
        )

    def _translate(self, exc: openai.APIError) -> LLMError:
        if isinstance(exc, openai.APIConnectionError):  # includes timeouts
            return LLMTransientError(f"{self.name} connection error: {type(exc).__name__}")
        if isinstance(exc, openai.APIStatusError):
            message = f"{self.name} HTTP {exc.status_code} (request {exc.request_id})"
            if exc.status_code in _RETRYABLE_STATUS or exc.status_code >= 500:
                return LLMTransientError(message)
            return LLMError(message)
        return LLMError(f"{self.name} error: {type(exc).__name__}")


class OpenAIProvider(OpenAIChatProvider):
    """OpenAI's own API (api.openai.com)."""

    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_sec: float,
        retry: RetryPolicy | None = None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        # SDK retries are off: the base class owns the retry policy.
        client = openai.AsyncOpenAI(
            api_key=api_key, timeout=timeout_sec, max_retries=0, http_client=http_client
        )
        super().__init__(client, model, retry)


def _messages(
    messages: Sequence[ChatMessage], system: str | None
) -> list[ChatCompletionMessageParam]:
    params: list[ChatCompletionMessageParam] = []
    if system:
        params.append({"role": "system", "content": system})
    for message in messages:
        if message.role == "user":
            params.append({"role": "user", "content": message.content})
        else:
            params.append({"role": "assistant", "content": message.content})
    return params


def _usage(usage: Any) -> TokenUsage:
    if usage is None:
        return TokenUsage()
    return TokenUsage(input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens)
