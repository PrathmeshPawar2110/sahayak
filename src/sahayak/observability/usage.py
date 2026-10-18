"""LLM usage and cost per unit of work (TRD §5 Observability: latency, tokens, cost).

Code that wants to know what its LLM calls cost opens a meter with `metered()`; every LLM call
made inside it (including in tasks started from it) is added to every open meter, so a turn's
meter and the current node's meter both see the call.

Costs use list prices per million tokens. A model without a price makes the cost unknown (None)
rather than wrong.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

# USD per million (input, output) tokens: OpenAI list prices for standard processing, matched by
# the longest prefix of the model name the API returns (e.g. "gpt-4o-2024-08-06"). Check against
# the provider's pricing page when adding or changing a model.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
}


def price(model: str) -> tuple[float, float] | None:
    matches = [prefix for prefix in PRICES_PER_MTOK if model.startswith(prefix)]
    return PRICES_PER_MTOK[max(matches, key=len)] if matches else None


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    rates = price(model)
    if rates is None:
        return None
    return (input_tokens * rates[0] + output_tokens * rates[1]) / 1_000_000


@dataclass
class UsageMeter:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = 0.0  # None once any call had an unknown price

    def add(self, model: str, input_tokens: int, output_tokens: int) -> None:
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        cost = cost_usd(model, input_tokens, output_tokens)
        self.cost_usd = None if cost is None or self.cost_usd is None else self.cost_usd + cost

    def fields(self) -> dict[str, int | float | None]:
        """For log events and span attributes."""
        return {
            "llm_calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": None if self.cost_usd is None else round(self.cost_usd, 6),
        }


_open_meters: ContextVar[tuple[UsageMeter, ...]] = ContextVar("llm_usage_meters", default=())


@contextmanager
def metered() -> Iterator[UsageMeter]:
    meter = UsageMeter()
    token = _open_meters.set((*_open_meters.get(), meter))
    try:
        yield meter
    finally:
        _open_meters.reset(token)


def record_llm_usage(model: str, input_tokens: int, output_tokens: int) -> None:
    for meter in _open_meters.get():
        meter.add(model, input_tokens, output_tokens)
