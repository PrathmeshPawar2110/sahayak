"""FR-14 for voice: values from a voice message the speech-to-text was unsure about are read back
and saved only once the user confirms them (M5-T07)."""

from typing import Any, cast

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from sahayak.agent.checkpoint import checkpoint_serializer
from sahayak.agent.deps import AgentConfig, AgentDeps
from sahayak.agent.graph import AgentGraph, build_graph
from sahayak.agent.replies import message
from sahayak.agent.state import AgentState, PendingQuestion, TurnUnderstanding
from tests.agent_support import AgentFakeLLM, agent_llm, make_deps, understood

LOW = 0.3
HIGH = 0.95


class Conversation:
    def __init__(self, *understandings: dict[str, Any], threshold: float = 0.6) -> None:
        self.llm: AgentFakeLLM = agent_llm(understandings)
        self.graph: AgentGraph = build_graph(InMemorySaver(serde=checkpoint_serializer()))
        self.deps: AgentDeps = make_deps(
            self.llm, config=AgentConfig(asr_confirm_threshold=threshold)
        )
        self.config: RunnableConfig = {"configurable": {"thread_id": "voice"}}

    async def say(self, text: str, *, confidence: float | None = None) -> AgentState:
        turn: AgentState = {
            "messages": [HumanMessage(text)],
            "consents": ["profile"],
            "asr_confidence": confidence,
        }
        return cast(AgentState, await self.graph.ainvoke(turn, self.config, context=self.deps))

    def understanding_prompts(self) -> list[str]:
        return [
            call.messages[0].content for call in self.llm.calls if call.schema is TurnUnderstanding
        ]


def _reply(state: AgentState) -> str:
    assert state["reply"] is not None
    return state["reply"].text


async def test_an_unclear_voice_message_is_read_back_instead_of_saved() -> None:
    chat = Conversation(understood(facts={"occupation": "farmer", "land_holding_acres": "2"}))

    state = await chat.say("I am a farmer with 2 acres", confidence=LOW)

    profile = state["profile"]
    assert profile.occupation is None
    assert profile.land_holding_acres is None
    assert state["pending_question"] == PendingQuestion(field="occupation", kind="confirm_heard")
    assert _reply(state) == message("confirm_heard", "en", heard="I am a farmer with 2 acres")
    unclear = state["contradictions"]
    assert [(c.field, c.new, c.reason, c.asked) for c in unclear] == [
        ("occupation", "farmer", "unclear_audio", True),
        ("land_holding_acres", 2.0, "unclear_audio", True),
    ]
    assert state["questions_asked"] == 1


async def test_confirmed_values_are_saved_and_the_prompt_lists_them() -> None:
    chat = Conversation(
        understood(facts={"occupation": "farmer", "land_holding_acres": "2"}),
        understood(facts={"occupation": "farmer", "land_holding_acres": "2"}),
    )
    await chat.say("I am a farmer with 2 acres", confidence=LOW)

    # The confirmation itself may be unclear audio too: a confirmed value is taken as given.
    state = await chat.say("yes", confidence=LOW)

    assert state["profile"].occupation == "farmer"
    assert state["profile"].land_holding_acres == 2.0
    assert state["profile"].field_sources["occupation"].confidence == LOW
    assert state["contradictions"] == []
    prompt = chat.understanding_prompts()[1]
    assert "read back what it heard" in prompt
    assert "- `occupation`: farmer" in prompt
    assert "- `land_holding_acres`: 2" in prompt


async def test_values_the_user_does_not_confirm_are_dropped() -> None:
    chat = Conversation(
        understood(facts={"occupation": "farmer", "land_holding_acres": "2"}),
        understood(facts={"land_holding_acres": "3"}),  # "no, three acres"
    )
    await chat.say("I am a farmer with 2 acres", confidence=LOW)

    state = await chat.say("no, three acres")

    assert state["profile"].land_holding_acres == 3.0
    assert state["profile"].occupation is None  # not confirmed: asked for again later
    assert state["contradictions"] == []
    assert state["pending_question"] is not None
    assert state["pending_question"].kind == "ask"


async def test_clear_audio_and_typed_text_are_saved_directly() -> None:
    chat = Conversation(understood(facts={"age": "45"}), understood(facts={"state": "MH"}))

    clear = await chat.say("I am 45", confidence=HIGH)
    typed = await chat.say("Maharashtra")

    assert clear["profile"].age == 45
    assert clear["profile"].field_sources["age"].confidence == HIGH
    assert typed["profile"].state == "MH"
    assert typed["profile"].field_sources["state"].confidence == 1.0
    assert typed["contradictions"] == []


async def test_unclear_audio_repeating_a_known_value_needs_no_confirmation() -> None:
    chat = Conversation(understood(facts={"age": "45"}), understood(facts={"age": "45"}))
    await chat.say("I am 45")

    state = await chat.say("45", confidence=LOW)

    assert state["profile"].age == 45
    assert state["contradictions"] == []
    assert state["pending_question"] is None or state["pending_question"].kind == "ask"


async def test_unclear_audio_changing_a_known_value_keeps_it_until_confirmed() -> None:
    chat = Conversation(understood(facts={"age": "45"}), understood(facts={"age": "47"}))
    await chat.say("I am 45")

    state = await chat.say("47", confidence=LOW)

    assert state["profile"].age == 45
    assert state["pending_question"] == PendingQuestion(field="age", kind="confirm_heard")
    assert state["contradictions"][0].current == 45


async def test_the_threshold_comes_from_the_config() -> None:
    chat = Conversation(understood(facts={"age": "45"}), threshold=0.2)

    state = await chat.say("I am 45", confidence=LOW)

    assert state["profile"].age == 45
