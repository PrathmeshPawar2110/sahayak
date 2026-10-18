"""Provider-agnostic LLM interface (TRD §7, NFR Reliability).

Implementations only translate one request into one vendor call and map vendor errors onto the
exception types below. The public `complete` / `structured` methods add what every provider
shares: retries with exponential backoff, a tracing span and a content-free log line.
"""

import asyncio
import random
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

import structlog
from opentelemetry import trace
from pydantic import BaseModel

from sahayak.observability.usage import cost_usd, record_llm_usage

log = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    usage: TokenUsage = field(default_factory=TokenUsage)


@dataclass(frozen=True)
class StructuredResponse[T: BaseModel]:
    value: T
    model: str
    usage: TokenUsage = field(default_factory=TokenUsage)


class _Result(Protocol):
    @property
    def model(self) -> str: ...
    @property
    def usage(self) -> TokenUsage: ...


class LLMError(Exception):
    """A request failed and retrying it will not help (bad request, auth, config...)."""


class LLMTransientError(LLMError):
    """Rate limit, timeout, connection error or 5xx: safe to retry."""


class LLMRefusalError(LLMError):
    """The model or a content filter declined the request."""


class LLMOutputError(LLMError):
    """The response was cut off or did not match the requested schema."""


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with full jitter. `attempts` counts the first try (TRD: 3 tries)."""

    attempts: int = 3
    base_delay_sec: float = 1.0
    max_delay_sec: float = 20.0
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    def delay(self, retry_number: int) -> float:
        ceiling = min(self.max_delay_sec, self.base_delay_sec * 2 ** (retry_number - 1))
        return random.uniform(ceiling / 2, ceiling)  # noqa: S311 (jitter, not crypto)


class LLMProvider(ABC):
    name: str

    def __init__(self, retry: RetryPolicy | None = None) -> None:
        self._retry = retry or RetryPolicy()

    @property
    @abstractmethod
    def model(self) -> str:
        """Model or deployment name, for logs, traces and provenance records."""

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        return await self._call(
            "complete", lambda: self._complete(messages, system=system, max_tokens=max_tokens)
        )

    async def structured[T: BaseModel](
        self,
        messages: Sequence[ChatMessage],
        schema: type[T],
        *,
        system: str | None = None,
        max_tokens: int = 16000,
    ) -> StructuredResponse[T]:
        """Return the model's answer parsed into `schema` (validated by Pydantic)."""
        return await self._call(
            "structured",
            lambda: self._structured(messages, schema, system=system, max_tokens=max_tokens),
        )

    @abstractmethod
    async def _complete(
        self, messages: Sequence[ChatMessage], *, system: str | None, max_tokens: int
    ) -> LLMResponse: ...

    @abstractmethod
    async def _structured[T: BaseModel](
        self,
        messages: Sequence[ChatMessage],
        schema: type[T],
        *,
        system: str | None,
        max_tokens: int,
    ) -> StructuredResponse[T]: ...

    async def _call[R: _Result](self, operation: str, request: Callable[[], Awaitable[R]]) -> R:
        with tracer.start_as_current_span(f"llm.{operation}") as span:
            span.set_attribute("llm.provider", self.name)
            span.set_attribute("llm.model", self.model)
            for attempt in range(1, self._retry.attempts + 1):
                started = time.perf_counter()
                try:
                    result = await request()
                except LLMTransientError as exc:
                    if attempt == self._retry.attempts:
                        log.warning(
                            "llm_call_failed", provider=self.name, attempts=attempt, error=str(exc)
                        )
                        raise
                    delay = self._retry.delay(attempt)
                    log.info(
                        "llm_call_retry",
                        provider=self.name,
                        attempt=attempt,
                        delay_sec=round(delay, 2),
                        error=str(exc),
                    )
                    await self._retry.sleep(delay)
                    continue
                latency_ms = round((time.perf_counter() - started) * 1000)
                usage = result.usage
                cost = cost_usd(result.model, usage.input_tokens, usage.output_tokens)
                record_llm_usage(result.model, usage.input_tokens, usage.output_tokens)
                span.set_attribute("llm.input_tokens", usage.input_tokens)
                span.set_attribute("llm.output_tokens", usage.output_tokens)
                span.set_attribute("llm.attempts", attempt)
                if cost is not None:
                    span.set_attribute("llm.cost_usd", cost)
                log.info(
                    "llm_call",
                    provider=self.name,
                    model=result.model,
                    operation=operation,
                    attempts=attempt,
                    latency_ms=latency_ms,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cost_usd=None if cost is None else round(cost, 6),
                )
                return result
        raise AssertionError("unreachable: the loop returns or raises")  # pragma: no cover
