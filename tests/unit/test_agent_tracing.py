import json
from collections.abc import Iterator

import pytest
import structlog
from langgraph.checkpoint.memory import InMemorySaver
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from sahayak.agent.checkpoint import checkpoint_serializer
from sahayak.agent.graph import build_graph
from sahayak.agent.service import AgentService
from sahayak.observability.usage import cost_usd, metered, price, record_llm_usage
from tests.agent_support import AgentFakeLLM, make_deps, understood


class PricedFakeLLM(AgentFakeLLM):
    @property
    def model(self) -> str:
        return "gpt-4o-2024-08-06"


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-4o", (2.50, 10.00)),
        ("gpt-4o-2024-08-06", (2.50, 10.00)),
        ("gpt-4o-mini-2024-07-18", (0.15, 0.60)),
        ("claude-opus-5-5", None),
    ],
)
def test_price_matches_the_longest_prefix(model: str, expected: object) -> None:
    assert price(model) == expected


def test_cost() -> None:
    assert cost_usd("gpt-4o", 1_000_000, 100_000) == pytest.approx(3.50)
    assert cost_usd("unknown", 10, 10) is None


def test_meters_nest_and_unknown_prices_make_the_cost_unknown() -> None:
    record_llm_usage("gpt-4o", 1, 1)  # no meter open: ignored
    with metered() as outer:
        record_llm_usage("gpt-4o", 1000, 100)
        with metered() as inner:
            record_llm_usage("gpt-4o", 1000, 100)
        assert inner.fields() == {
            "llm_calls": 1,
            "input_tokens": 1000,
            "output_tokens": 100,
            "cost_usd": 0.0035,
        }
        record_llm_usage("some-new-model", 10, 10)
    assert (outer.calls, outer.input_tokens, outer.output_tokens) == (3, 2010, 210)
    assert outer.cost_usd is None


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter
    exporter.shutdown()


async def test_turns_and_nodes_are_traced_without_text(spans: InMemorySpanExporter) -> None:
    llm = PricedFakeLLM([understood(facts={"age": "65", "state": "UP"})])
    service = AgentService(
        build_graph(InMemorySaver(serde=checkpoint_serializer())), make_deps(llm)
    )
    with structlog.testing.capture_logs() as logs:
        result = await service.run_turn(
            "s1", "I am Rameshwar, 65, from UP", language="en", consents=["profile"]
        )

    nodes = [e for e in logs if e["event"] == "agent_node"]
    assert [e["node"] for e in nodes] == [
        "load_user_context",
        "classify_intent",
        "extract_profile",
        "shortlist_schemes",
        "check_eligibility",
        "filter_results",
        "plan_question",
    ]
    classify = nodes[1]
    assert (classify["status"], classify["llm_calls"]) == ("ok", 1)
    assert classify["input_tokens"] > 0
    assert classify["cost_usd"] > 0
    assert nodes[0]["llm_calls"] == 0
    (turn,) = [e for e in logs if e["event"] == "agent_turn"]
    assert turn["llm_calls"] == 2  # understanding + question wording
    assert turn["kind"] == "question"
    assert result.usage.calls == 2
    assert "Rameshwar" not in json.dumps(logs, default=str)  # no message text in any event

    finished = {span.name: span for span in spans.get_finished_spans()}
    node_span = finished["agent.classify_intent"]
    assert node_span.attributes is not None
    assert node_span.attributes["agent.llm_calls"] == 1
    llm_span = finished["llm.structured"]
    assert llm_span.parent is not None
    turn_span = finished["agent.turn"]
    assert turn_span.attributes is not None
    assert turn_span.attributes["agent.llm_calls"] == 2
    assert all(
        "Rameshwar" not in str(value)
        for span in finished.values()
        for value in (span.attributes or {}).values()
    )
