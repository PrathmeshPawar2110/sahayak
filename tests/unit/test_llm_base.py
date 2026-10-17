import pytest
from pydantic import BaseModel

from sahayak.config import Settings
from sahayak.llm.anthropic import AnthropicProvider
from sahayak.llm.azure_openai import AzureOpenAIProvider
from sahayak.llm.base import ChatMessage, LLMError, LLMTransientError, RetryPolicy
from sahayak.llm.factory import LLMConfigError, create_llm_provider
from sahayak.llm.fake import FakeCall, FakeLLMProvider
from sahayak.llm.openai_chat import OpenAIProvider


class Answer(BaseModel):
    value: int


HELLO = [ChatMessage(role="user", content="hello there")]


@pytest.mark.parametrize("retry_number", [1, 2, 3, 10])
def test_backoff_grows_exponentially_and_is_capped(retry_number: int) -> None:
    policy = RetryPolicy(base_delay_sec=1.0, max_delay_sec=5.0)
    ceiling = min(5.0, 2 ** (retry_number - 1))

    delays = [policy.delay(retry_number) for _ in range(50)]

    assert all(ceiling / 2 <= d <= ceiling for d in delays)


async def test_retries_sleep_between_attempts() -> None:
    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)

    fake = FakeLLMProvider(
        [LLMTransientError("429"), LLMTransientError("503"), "done"],
        retry=RetryPolicy(base_delay_sec=1.0, sleep=record),
    )

    result = await fake.complete(HELLO)

    assert result.text == "done"
    assert len(slept) == 2
    assert 0.5 <= slept[0] <= 1.0
    assert 1.0 <= slept[1] <= 2.0


async def test_non_transient_errors_are_raised_immediately() -> None:
    fake = FakeLLMProvider([LLMError("bad request"), "never reached"])

    with pytest.raises(LLMError, match="bad request"):
        await fake.complete(HELLO)

    assert len(fake.calls) == 1


async def test_fake_validates_dict_replies_against_the_schema() -> None:
    fake = FakeLLMProvider([{"value": 3}])

    result = await fake.structured(HELLO, Answer, system="be brief")

    assert result.value == Answer(value=3)
    assert fake.calls == [FakeCall("structured", HELLO, "be brief", Answer)]


async def test_fake_can_compute_replies_from_the_call() -> None:
    fake = FakeLLMProvider(lambda call: call.messages[-1].content.upper())

    assert (await fake.complete(HELLO)).text == "HELLO THERE"


async def test_fake_without_replies_fails_loudly() -> None:
    with pytest.raises(LLMError, match="no scripted reply"):
        await FakeLLMProvider().complete(HELLO)


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_factory_builds_azure_by_default() -> None:
    settings = make_settings(
        azure_openai_endpoint="https://x.openai.azure.com",
        azure_openai_api_key="k",
        azure_openai_deployment="gpt-4o",
    )

    provider = create_llm_provider(settings)

    assert isinstance(provider, AzureOpenAIProvider)
    assert provider.model == "gpt-4o"


def test_factory_builds_openai() -> None:
    provider = create_llm_provider(make_settings(llm_provider="openai", openai_api_key="k"))

    assert isinstance(provider, OpenAIProvider)
    assert provider.model == "gpt-4o"


def test_factory_builds_anthropic() -> None:
    provider = create_llm_provider(make_settings(llm_provider="anthropic", anthropic_api_key="k"))

    assert isinstance(provider, AnthropicProvider)
    assert provider.model == "claude-opus-5-5"


@pytest.mark.parametrize(
    ("overrides", "missing"),
    [
        ({}, "AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT"),
        ({"llm_provider": "anthropic"}, "ANTHROPIC_API_KEY"),
        ({"llm_provider": "openai"}, "OPENAI_API_KEY"),
        ({"llm_provider": "openai", "openai_api_key": "k", "openai_model": ""}, "OPENAI_MODEL"),
        ({"llm_provider": "anthropic", "anthropic_api_key": "k", "anthropic_model": ""}, "MODEL"),
    ],
)
def test_factory_reports_missing_settings(overrides: dict[str, object], missing: str) -> None:
    with pytest.raises(LLMConfigError, match=missing):
        create_llm_provider(make_settings(**overrides))
