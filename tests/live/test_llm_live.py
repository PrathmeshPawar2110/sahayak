"""Calls the configured LLM provider for real. Run with `uv run pytest -m live` (costs money)."""

import pytest
from pydantic import BaseModel

from sahayak.config import Settings
from sahayak.llm.base import ChatMessage
from sahayak.llm.factory import LLMConfigError, create_llm_provider

pytestmark = pytest.mark.live


class Capital(BaseModel):
    country: str
    capital: str


async def test_configured_provider_returns_structured_output() -> None:
    try:
        provider = create_llm_provider(Settings())
    except LLMConfigError as exc:
        pytest.skip(str(exc))

    result = await provider.structured(
        [ChatMessage(role="user", content="What is the capital of India?")],
        Capital,
        max_tokens=2000,
    )

    assert result.value.capital.lower() == "new delhi"
    assert result.usage.output_tokens > 0
