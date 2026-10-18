from datetime import UTC, datetime
from typing import Any, cast

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.base import BasePostgresSaver
from pydantic import BaseModel

from sahayak.agent.checkpoint import (
    CHECKPOINT_MIGRATIONS,
    STATE_TYPES,
    checkpoint_serializer,
    psycopg_conninfo,
)
from sahayak.agent.graph import build_graph
from sahayak.agent.state import (
    AgentReply,
    AgentState,
    Citation,
    Contradiction,
    DisplayItem,
    DisplayResults,
    DocumentSummary,
    EvidenceChunk,
    ExtractedFact,
    PendingQuestion,
    TurnUnderstanding,
)
from sahayak.eligibility.engine import evaluate
from sahayak.eligibility.profile import FieldSource, UserProfile
from tests.agent_support import FARMER, make_deps


def _roundtrip(value: object) -> object:
    serde = checkpoint_serializer()
    return serde.loads_typed(serde.dumps_typed(value))


def _populated_state() -> dict[str, Any]:
    profile = UserProfile(
        language="mr",
        age=45,
        occupation="farmer",
        land_holding_acres=2.0,
        field_sources={
            "age": FieldSource(
                source="self_declared", captured_at=datetime(2026, 10, 18, tzinfo=UTC), confidence=1
            )
        },
    )
    assert FARMER.rule is not None
    return {
        "profile": profile,
        "eligibility": {"farm-aid": evaluate(FARMER.rule, profile)},
        "pending_question": PendingQuestion(field="age", kind="ask"),
        "contradictions": [Contradiction(field="age", current=45, new=54)],
        "retrieved_context": [
            EvidenceChunk(
                chunk_id="c1",
                scheme_id="farm-aid",
                source_url="https://x",
                section="Benefits",
                language="en",
                text="t",
                last_verified_at=datetime(2026, 10, 17, tzinfo=UTC),
            )
        ],
        "display_results": DisplayResults(
            ready=[DisplayItem(scheme_id="farm-aid", rule_version=1, reasons=["age >= 18"])]
        ),
        "documents": [DocumentSummary(type="income_certificate", status="valid")],
        "understanding": TurnUnderstanding(
            intent="discover",
            language="mr",
            facts=[ExtractedFact(field="age", value="45")],
            declined_fields=["religion"],
            scheme_names=["PM Kisan"],
            search_query="farmer support",
        ),
        "reply": AgentReply(
            text="hi",
            kind="answer",
            citations=[
                Citation(number=1, chunk_id="c1", scheme_id="farm-aid", section="B", source_url="u")
            ],
        ),
        "messages": [HumanMessage("नमस्कार"), AIMessage("hello")],
    }


@pytest.mark.parametrize("key", list(_populated_state()))
def test_state_values_survive_the_strict_serializer(key: str) -> None:
    value = _populated_state()[key]
    assert _roundtrip(value) == value


def _stored_models(value: object) -> set[type[BaseModel]]:
    """Model classes stored as a state value or inside its lists and dicts (not nested fields)."""
    if isinstance(value, BaseModel):
        return set() if type(value).__module__.startswith("langchain") else {type(value)}
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return set().union(*(_stored_models(item) for item in value))
    return set()


def test_every_model_stored_in_state_is_allowlisted() -> None:
    stored = set().union(*(_stored_models(v) for v in _populated_state().values()))
    assert stored <= set(STATE_TYPES)


def test_unlisted_classes_are_not_rebuilt() -> None:
    class Sneaky(BaseModel):
        x: int

    assert _roundtrip(Sneaky(x=1)) == {"x": 1}


def test_alembic_applies_every_library_migration() -> None:
    assert len(BasePostgresSaver.MIGRATIONS) == CHECKPOINT_MIGRATIONS


def test_psycopg_conninfo_drops_the_sqlalchemy_driver() -> None:
    url = "postgresql+asyncpg://user:pa%40ss@db.example:5433/sahayak"
    assert psycopg_conninfo(url) == "postgresql://user:pa%40ss@db.example:5433/sahayak"


async def test_state_is_kept_per_session_thread() -> None:
    graph = build_graph(InMemorySaver(serde=checkpoint_serializer()))
    deps = make_deps()

    async def turn(thread: str) -> AgentState:
        config: RunnableConfig = {"configurable": {"thread_id": thread}}
        state = await graph.ainvoke(
            {"session_id": thread, "messages": [HumanMessage("hi")]}, config, context=deps
        )
        return cast(AgentState, state)

    await turn("a")
    second = await turn("a")
    other = await turn("b")
    assert second["turn_count"] == 2
    assert len(second["messages"]) == 2
    assert other["turn_count"] == 1
    assert second["profile"] == UserProfile(language="en")
