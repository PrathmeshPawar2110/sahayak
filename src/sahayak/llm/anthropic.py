"""Anthropic Claude implementation of `LLMProvider`."""

from collections.abc import Sequence

import anthropic
import httpx2
from anthropic.types.beta import BetaMessage, BetaMessageParam, BetaOutputConfigParam
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

# Server-side refusal fallback: if a safety classifier declines, the API re-runs the request on a
# fallback model within the same call instead of returning an empty refusal.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
_RETRYABLE_STATUS = frozenset({408, 409, 429})


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_sec: float,
        retry: RetryPolicy | None = None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        super().__init__(retry)
        self._model = model
        # SDK retries are off: the base class owns the retry policy.
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key, timeout=timeout_sec, max_retries=0, http_client=http_client
        )

    @property
    def model(self) -> str:
        return self._model

    async def _complete(
        self, messages: Sequence[ChatMessage], *, system: str | None, max_tokens: int
    ) -> LLMResponse:
        response = await self._create(messages, system, max_tokens, output_config=None)
        return LLMResponse(text=_text(response), model=response.model, usage=_usage(response))

    async def _structured[T: BaseModel](
        self,
        messages: Sequence[ChatMessage],
        schema: type[T],
        *,
        system: str | None,
        max_tokens: int,
    ) -> StructuredResponse[T]:
        output_config: BetaOutputConfigParam = {
            "format": {"type": "json_schema", "schema": anthropic.transform_schema(schema)}
        }
        response = await self._create(messages, system, max_tokens, output_config=output_config)
        try:
            value = schema.model_validate_json(_text(response))
        except ValidationError as exc:
            raise LLMOutputError("the response did not match the schema") from exc
        return StructuredResponse(value=value, model=response.model, usage=_usage(response))

    async def _create(
        self,
        messages: Sequence[ChatMessage],
        system: str | None,
        max_tokens: int,
        *,
        output_config: BetaOutputConfigParam | None,
    ) -> BetaMessage:
        params: list[BetaMessageParam] = [{"role": m.role, "content": m.content} for m in messages]
        try:
            response = await self._client.beta.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                messages=params,
                system=system or anthropic.omit,
                output_config=output_config or anthropic.omit,
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.APIError as exc:
            raise _translate(exc) from exc
        # Checked before any parsing: a refusal or a cut-off response has no valid output.
        if response.stop_reason == "refusal":
            raise LLMRefusalError("the model declined the request")
        if response.stop_reason == "max_tokens":
            raise LLMOutputError("the response hit max_tokens and was cut off")
        return response


def _text(response: BetaMessage) -> str:
    return "".join(block.text for block in response.content if block.type == "text")


def _usage(response: BetaMessage) -> TokenUsage:
    return TokenUsage(
        input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens
    )


def _translate(exc: anthropic.APIError) -> LLMError:
    if isinstance(exc, anthropic.APIConnectionError):  # includes timeouts
        return LLMTransientError(f"anthropic connection error: {type(exc).__name__}")
    if isinstance(exc, anthropic.APIStatusError):
        message = f"anthropic HTTP {exc.status_code} (request {exc.request_id})"
        if exc.status_code in _RETRYABLE_STATUS or exc.status_code >= 500:
            return LLMTransientError(message)
        return LLMError(message)
    return LLMError(f"anthropic error: {type(exc).__name__}")
