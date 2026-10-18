from typing import Any, cast

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from sahayak.agent.checkpoint import checkpoint_serializer
from sahayak.agent.deps import AgentDeps
from sahayak.agent.graph import AgentGraph, build_graph, route_intent
from sahayak.agent.state import AgentState, Intent, PendingQuestion
from sahayak.eligibility.profile import UserProfile
from tests.agent_support import CATALOGUE, agent_llm, make_deps, understood


@pytest.mark.parametrize(
    ("intent", "node"),
    [
        ("discover", "extract_profile"),
        ("ask_scheme_info", "extract_profile"),
        ("select_scheme", "extract_profile"),
        ("get_form", "extract_profile"),
        ("reset", "handle_reset"),
        ("delete_account", "handle_reset"),
        ("smalltalk", "compose_response"),
        ("out_of_scope", "compose_response"),
        ("upload_document", "compose_response"),
        ("my_documents", "compose_response"),
        ("manage_reminders", "compose_response"),
    ],
)
def test_route_intent(intent: Intent, node: str) -> None:
    assert route_intent({"intent": intent}) == node


class Conversation:
    def __init__(self, *understandings: dict[str, Any]) -> None:
        self.graph: AgentGraph = build_graph(InMemorySaver(serde=checkpoint_serializer()))
        self.deps: AgentDeps = make_deps(agent_llm(understandings))
        self.config: RunnableConfig = {"configurable": {"thread_id": "conversation"}}

    async def say(self, text: str, **extra: Any) -> AgentState:
        turn: AgentState = {"messages": [HumanMessage(text)], "consents": ["profile"]}
        turn.update(extra)  # type: ignore[typeddict-item]
        state = await self.graph.ainvoke(turn, self.config, context=self.deps)
        return cast(AgentState, state)


def _reply(state: AgentState) -> str:
    assert state["reply"] is not None
    return state["reply"].text


async def test_a_conversation_asks_then_shows_only_eligible_schemes() -> None:
    chat = Conversation(
        understood(facts={"age": "65", "occupation": "farmer", "state": "UP"}),
        understood(facts={"land_holding_acres": "2"}),
        understood(facts={"residence": "rural"}),
    )
    first = await chat.say("I am a 65 year old farmer in UP")
    assert first["pending_question"] == PendingQuestion(field="land_holding_acres", kind="ask")
    second = await chat.say("2 acres")
    assert second["pending_question"] == PendingQuestion(field="residence", kind="ask")
    third = await chat.say("in a village")

    results = third["display_results"]
    assert [i.scheme_id for i in results.ready] == ["farm-aid", "old-pension"]
    assert [i.scheme_id for i in results.provisional] == ["new-housing"]
    assert third["pending_question"] is None
    assert third["questions_asked"] == 2
    reply = _reply(third)
    assert reply.startswith("Here is what I found.")
    assert "Not yet verified: my check for New Housing Mission" in reply
    assert len(third["messages"]) == 6
    for message in third["messages"]:  # no reply ever named a scheme the user can't get
        if isinstance(message, AIMessage):
            mentioned = CATALOGUE.mentions(str(message.content))
            assert set(mentioned) <= set(results.scheme_ids())


async def test_a_contradiction_is_confirmed_before_it_changes_the_profile() -> None:
    chat = Conversation(
        understood(facts={"age": "45"}),
        understood(facts={"age": "54"}),
        understood(facts={"age": "54"}),
    )
    await chat.say("I am 45")
    asked = await chat.say("Actually I am 54")
    assert asked["profile"].age == 45
    assert asked["pending_question"] == PendingQuestion(field="age", kind="confirm")
    assert "earlier you said 45, and now 54" in _reply(asked)
    confirmed = await chat.say("54")
    assert confirmed["profile"].age == 54
    assert confirmed["contradictions"] == []


@pytest.mark.parametrize(
    ("intent", "start"),
    [("reset", "I've cleared what you told me."), ("delete_account", "Your conversation")],
)
async def test_reset_forgets_the_conversation(intent: str, start: str) -> None:
    chat = Conversation(understood(facts={"age": "65", "state": "UP"}), understood(intent))
    await chat.say("I am 65, from UP")
    after = await chat.say("start over")
    assert _reply(after).startswith(start)
    assert after["profile"] == UserProfile(language="en")
    assert after["questions_asked"] == 0
    assert after["messages"] == [AIMessage(_reply(after), id=after["messages"][0].id)]


async def test_smalltalk_keeps_the_profile_and_needs_one_llm_call() -> None:
    chat = Conversation(understood(facts={"age": "65"}), understood("smalltalk", language="hi"))
    await chat.say("I am 65")
    calls_before = len(chat.deps.llm.calls)  # type: ignore[attr-defined]
    after = await chat.say("धन्यवाद")
    assert _reply(after).startswith("मैं सहायक हूँ।")
    assert after["profile"].age == 65
    assert after["language"] == "hi"
    assert len(chat.deps.llm.calls) - calls_before == 1  # type: ignore[attr-defined]


async def test_the_pii_notice_comes_with_the_reply() -> None:
    chat = Conversation(understood("smalltalk"))
    after = await chat.say("my aadhaar is XXXX-XXXX-0123", pii_found=["aadhaar"])
    assert _reply(after).startswith("Please don't share Aadhaar")
