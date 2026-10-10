"""Azure OpenAI implementation of `LLMProvider` (the default, TRD §7)."""

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


class AzureOpenAIProvider(LLMProvider):
    name = "azure_openai"

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        deployment: str,
        api_version: str,
        timeout_sec: float,
        retry: RetryPolicy | None = None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        super().__init__(retry)
        self._deployment = deployment
        # SDK retries are off: the base class owns the retry policy.
        self._client = openai.AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            timeout=timeout_sec,
            max_retries=0,
            http_client=http_client,
        )

    @property
    def model(self) -> str:
        return self._deployment

    async def _complete(
        self, messages: Sequence[ChatMessage], *, system: str | None, max_tokens: int
    ) -> LLMResponse:
        try:
            response = await self._client.chat.completions.create(
                model=self._deployment,
                messages=_messages(messages, system),
                max_completion_tokens=max_tokens,
            )
        except openai.APIError as exc:
            raise _translate(exc) from exc
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
                model=self._deployment,
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
            raise _translate(exc) from exc
        message = response.choices[0].message
        if message.refusal:
            raise LLMRefusalError("the model declined the request")
        if message.parsed is None:
            raise LLMOutputError("response did not contain the structured output")
        return StructuredResponse(
            value=message.parsed, model=response.model, usage=_usage(response.usage)
        )


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


def _translate(exc: openai.APIError) -> LLMError:
    if isinstance(exc, openai.APIConnectionError):  # includes timeouts
        return LLMTransientError(f"azure openai connection error: {type(exc).__name__}")
    if isinstance(exc, openai.APIStatusError):
        message = f"azure openai HTTP {exc.status_code} (request {exc.request_id})"
        if exc.status_code in _RETRYABLE_STATUS or exc.status_code >= 500:
            return LLMTransientError(message)
        return LLMError(message)
    return LLMError(f"azure openai error: {type(exc).__name__}")
