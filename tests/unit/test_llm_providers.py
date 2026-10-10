"""Contract tests every `LLMProvider` must pass (TRD §16). HTTP is served by an in-memory
transport, so no request leaves the process."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx2
import pytest
from pydantic import BaseModel

from sahayak.llm.anthropic import AnthropicProvider
from sahayak.llm.azure_openai import AzureOpenAIProvider
from sahayak.llm.base import (
    ChatMessage,
    LLMError,
    LLMOutputError,
    LLMProvider,
    LLMRefusalError,
    LLMTransientError,
    RetryPolicy,
)

Handler = Callable[[httpx2.Request], httpx2.Response]


class Verdict(BaseModel):
    scheme: str
    eligible: bool


async def no_sleep(_seconds: float) -> None:
    return None


@dataclass
class Harness:
    """Speaks one vendor's wire format."""

    name: str
    build: Callable[[httpx2.AsyncClient], LLMProvider]
    ok: Callable[[str], dict[str, Any]]
    refusal: Callable[[], dict[str, Any]]
    truncated: Callable[[], dict[str, Any]]
    system_of: Callable[[dict[str, Any]], str | None]


def anthropic_message(text: str, stop_reason: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 7},
    }


def openai_completion(
    content: str | None, finish_reason: str = "stop", refusal: str | None = None
) -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-4o",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content, "refusal": refusal},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
    }


def anthropic_system(body: dict[str, Any]) -> str | None:
    return body.get("system")


def openai_system(body: dict[str, Any]) -> str | None:
    first = body["messages"][0]
    return first["content"] if first["role"] == "system" else None


HARNESSES = [
    Harness(
        name="anthropic",
        build=lambda http: AnthropicProvider(
            api_key="test-key",
            model="claude-opus-5-5",
            timeout_sec=5,
            retry=RetryPolicy(sleep=no_sleep),
            http_client=http,
        ),
        ok=anthropic_message,
        refusal=lambda: anthropic_message("", stop_reason="refusal"),
        truncated=lambda: anthropic_message('{"scheme": "pm-', stop_reason="max_tokens"),
        system_of=anthropic_system,
    ),
    Harness(
        name="azure_openai",
        build=lambda http: AzureOpenAIProvider(
            endpoint="https://example.openai.azure.com",
            api_key="test-key",
            deployment="gpt-4o",
            api_version="2024-10-21",
            timeout_sec=5,
            retry=RetryPolicy(sleep=no_sleep),
            http_client=http,
        ),
        ok=openai_completion,
        refusal=lambda: openai_completion(None, refusal="I can't help with that."),
        truncated=lambda: openai_completion('{"scheme": "pm-', finish_reason="length"),
        system_of=openai_system,
    ),
]


@pytest.fixture(params=HARNESSES, ids=lambda h: h.name)
def harness(request: pytest.FixtureRequest) -> Harness:
    harness: Harness = request.param
    return harness


class Server:
    """Replays queued responses and records the requests it received."""

    def __init__(self, *responses: httpx2.Response | Exception) -> None:
        self.queue = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        response = self.queue.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def body(self, index: int = 0) -> dict[str, Any]:
        body: dict[str, Any] = json.loads(self.requests[index].content)
        return body


def provider_for(harness: Harness, server: Server) -> LLMProvider:
    return harness.build(httpx2.AsyncClient(transport=httpx2.MockTransport(server)))


def ok(payload: dict[str, Any]) -> httpx2.Response:
    return httpx2.Response(200, json=payload)


def error(status: int) -> httpx2.Response:
    return httpx2.Response(status, json={"error": {"type": "error", "message": "boom"}})


QUESTION = [ChatMessage(role="user", content="Is a farmer with 2 acres eligible for PM-KISAN?")]
VERDICT_JSON = json.dumps({"scheme": "pm-kisan", "eligible": True})


async def test_structured_output_is_parsed_into_the_schema(harness: Harness) -> None:
    server = Server(ok(harness.ok(VERDICT_JSON)))

    result = await provider_for(harness, server).structured(
        QUESTION, Verdict, system="Answer as JSON."
    )

    assert result.value == Verdict(scheme="pm-kisan", eligible=True)
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 7
    assert harness.system_of(server.body()) == "Answer as JSON."


async def test_complete_returns_text(harness: Harness) -> None:
    server = Server(ok(harness.ok("नमस्ते")))

    result = await provider_for(harness, server).complete(QUESTION)

    assert result.text == "नमस्ते"
    assert harness.system_of(server.body()) is None


@pytest.mark.parametrize("status", [429, 500, 503, 529])
async def test_transient_errors_are_retried(harness: Harness, status: int) -> None:
    server = Server(error(status), ok(harness.ok(VERDICT_JSON)))

    result = await provider_for(harness, server).structured(QUESTION, Verdict)

    assert result.value.eligible is True
    assert len(server.requests) == 2


async def test_gives_up_after_three_attempts(harness: Harness) -> None:
    server = Server(error(500), error(502), error(503))

    with pytest.raises(LLMTransientError):
        await provider_for(harness, server).structured(QUESTION, Verdict)

    assert len(server.requests) == 3


async def test_connection_errors_are_retried(harness: Harness) -> None:
    server = Server(httpx2.ConnectError("refused"), ok(harness.ok("ok")))

    result = await provider_for(harness, server).complete(QUESTION)

    assert result.text == "ok"
    assert len(server.requests) == 2


@pytest.mark.parametrize("status", [400, 401, 404])
async def test_client_errors_are_not_retried(harness: Harness, status: int) -> None:
    server = Server(error(status))

    with pytest.raises(LLMError) as raised:
        await provider_for(harness, server).complete(QUESTION)

    assert not isinstance(raised.value, LLMTransientError)
    assert len(server.requests) == 1


async def test_refusal_raises(harness: Harness) -> None:
    server = Server(ok(harness.refusal()))

    with pytest.raises(LLMRefusalError):
        await provider_for(harness, server).structured(QUESTION, Verdict)

    assert len(server.requests) == 1


async def test_truncated_output_raises(harness: Harness) -> None:
    server = Server(ok(harness.truncated()))

    with pytest.raises(LLMOutputError):
        await provider_for(harness, server).structured(QUESTION, Verdict)


async def test_output_not_matching_the_schema_raises(harness: Harness) -> None:
    server = Server(ok(harness.ok(json.dumps({"scheme": "pm-kisan"}))))

    with pytest.raises(LLMOutputError):
        await provider_for(harness, server).structured(QUESTION, Verdict)


async def test_anthropic_requests_server_side_refusal_fallback() -> None:
    server = Server(ok(anthropic_message("ok")))

    await provider_for(HARNESSES[0], server).complete(QUESTION)

    assert server.body()["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in server.requests[0].headers["anthropic-beta"]
    assert server.body()["model"] == "claude-opus-5-5"


async def test_azure_targets_the_deployment_and_api_version() -> None:
    server = Server(ok(openai_completion("ok")))

    await provider_for(HARNESSES[1], server).complete(QUESTION)

    url = server.requests[0].url
    assert url.path == "/openai/deployments/gpt-4o/chat/completions"
    assert url.params["api-version"] == "2024-10-21"


async def test_anthropic_sends_the_schema_as_output_config() -> None:
    server = Server(ok(anthropic_message(VERDICT_JSON)))

    await provider_for(HARNESSES[0], server).structured(QUESTION, Verdict)

    output_format = server.body()["output_config"]["format"]
    assert output_format["type"] == "json_schema"
    assert set(output_format["schema"]["properties"]) == {"scheme", "eligible"}


async def test_azure_sends_a_strict_json_schema() -> None:
    server = Server(ok(openai_completion(VERDICT_JSON)))

    await provider_for(HARNESSES[1], server).structured(QUESTION, Verdict)

    response_format = server.body()["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
