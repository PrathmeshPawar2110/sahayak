import string
from datetime import UTC, date, datetime
from typing import Any, cast

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.runtime import Runtime

from sahayak.agent.checkpoint import checkpoint_serializer
from sahayak.agent.deps import AgentConfig, AgentDeps
from sahayak.agent.graph import build_graph
from sahayak.agent.nodes.plan_question import QuestionWording, plan_question
from sahayak.agent.questions import SENSITIVE_FIELDS, choose_question, should_ask
from sahayak.agent.replies import LANGUAGES, format_date, format_value, load_replies, question
from sahayak.agent.state import AgentState, Contradiction, PendingQuestion
from sahayak.eligibility.engine import evaluate_all
from sahayak.eligibility.fields import PROFILE_FIELDS
from sahayak.eligibility.profile import UserProfile
from sahayak.llm.base import LLMTransientError
from sahayak.llm.fake import FakeCall
from tests.agent_support import CATALOGUE, agent_llm, make_deps, understood


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_every_reply_exists_in_every_language_with_the_same_placeholders() -> None:
    for section, entries in load_replies().items():
        for key, texts in entries.items():
            assert set(texts) == set(LANGUAGES), (section, key)
            assert all(isinstance(text, str) and text.strip() for text in texts.values())
            assert len({frozenset(_placeholders(text)) for text in texts.values()}) == 1, key


def test_every_profile_field_has_a_question() -> None:
    assert set(load_replies()["questions"]) == set(PROFILE_FIELDS)
    for field in SENSITIVE_FIELDS:  # sensitive questions say they can be skipped
        assert "skip" in question(field, "en")


@pytest.mark.parametrize(
    ("value", "language", "expected"),
    [
        (True, "en", "yes"),
        (False, "hi", "नहीं"),
        (True, "mr", "हो"),
        (2.0, "en", "2"),
        (2.5, "en", "2.5"),
        (45, "hi", "45"),
        ("agri_labourer", "en", "agri labourer"),
    ],
)
def test_format_value(value: Any, language: Any, expected: str) -> None:
    assert format_value(value, language) == expected


def test_format_date() -> None:
    assert format_date(datetime(2026, 10, 7, 9, tzinfo=UTC)) == "07-10-2026"
    assert format_date(date(2026, 1, 31)) == "31-01-2026"
    assert format_date(None) == ""


def _state(profile: dict[str, Any] | None = None, **extra: Any) -> AgentState:
    user = UserProfile.model_validate({"language": "en", **(profile or {})})
    rules = [s.rule for s in CATALOGUE if s.rule is not None]
    state: AgentState = {
        "intent": "discover",
        "language": "en",
        "profile": user,
        "eligibility": evaluate_all(rules, user),
        "consents": ["profile"],
        "messages": [HumanMessage("I am a farmer")],
    }
    state.update(extra)  # type: ignore[typeddict-item]
    return state


def test_choose_question_prefers_the_named_schemes() -> None:
    config = AgentConfig()
    everything = choose_question(_state({"occupation": "farmer"}), config)
    assert everything is not None
    focused = choose_question(
        _state({"occupation": "farmer"}, focus_schemes=["old-pension"]), config
    )
    assert focused is not None
    assert (focused.field, focused.scheme_ids) == ("age", ("old-pension",))


def test_sensitive_fields_are_only_asked_with_consent() -> None:
    sensitive_rule = {"all": [{"field": "social_category", "op": "==", "value": "sc"}]}
    from tests.agent_support import rule

    user = UserProfile(language="en")
    results = evaluate_all([rule("cat", sensitive_rule)], user)
    base = _state(eligibility=results)
    assert choose_question(base, AgentConfig()) is None
    with_consent = _state(eligibility=results, consents=["profile", "sensitive"])
    planned = choose_question(with_consent, AgentConfig())
    assert planned is not None
    assert (planned.field, planned.sensitive) == ("social_category", True)


def test_should_ask() -> None:
    config = AgentConfig(max_questions=2)
    assert should_ask(_state(), config)
    assert not should_ask(_state(intent="ask_scheme_info"), config)
    assert not should_ask(_state(questions_asked=2), config)
    contradiction = [Contradiction(field="age", current=45, new=54)]
    assert should_ask(_state({"age": 45}, contradictions=contradiction), config)
    assert not should_ask(
        _state({"age": 45}, contradictions=contradiction, questions_asked=2), config
    )


def _wording(text: str) -> dict[type, Any]:
    return {QuestionWording: lambda call: {"question": text}}


async def _plan(deps: AgentDeps, state: AgentState) -> AgentState:
    return await plan_question(state, Runtime(context=deps))


async def test_plan_question_uses_the_llm_wording() -> None:
    llm = agent_llm(others=_wording("  Thank you.   How much land do you own?  "))
    update = await _plan(
        make_deps(llm), _state({"occupation": "farmer"}, focus_schemes=["farm-aid"])
    )
    reply = update["reply"]
    assert reply is not None
    assert (reply.text, reply.kind, reply.question_field) == (
        "Thank you. How much land do you own?",
        "question",
        "land_holding_acres",
    )
    assert update["pending_question"] == PendingQuestion(field="land_holding_acres", kind="ask")
    assert update["questions_asked"] == 1
    assert [m.content for m in update["messages"]] == [reply.text]
    (call,) = [c for c in llm.calls if c.schema is QuestionWording]
    assert "land_holding_acres" in call.messages[0].content
    assert "Never name or describe any government scheme" in (call.system or "")


@pytest.mark.parametrize(
    "responder",
    [
        lambda call: LLMTransientError("down"),
        lambda call: {"question": "Do you own land for the Farm Aid Scheme?"},  # names a scheme
        lambda call: {"question": "   "},
        lambda call: {"question": "x" * 500},
    ],
)
async def test_plan_question_falls_back_to_fixed_wording(responder: Any) -> None:
    def respond(call: FakeCall) -> Any:
        return responder(call)

    deps = make_deps(agent_llm(others={QuestionWording: respond}))
    state = _state({"occupation": "farmer"}, focus_schemes=["farm-aid"], language="mr")
    update = await _plan(deps, state)
    reply = update["reply"]
    assert reply is not None
    assert reply.text == question("land_holding_acres", "mr")


async def test_a_contradiction_is_confirmed_first_with_fixed_wording() -> None:
    contradiction = Contradiction(field="age", current=45, new=54)
    deps = make_deps(agent_llm())  # no wording call expected
    update = await _plan(deps, _state({"age": 45}, contradictions=[contradiction], language="hi"))
    reply = update["reply"]
    assert reply is not None
    assert reply.text == "एक बार पक्का कर लें: पहले आपने 45 बताया था, और अब 54। इनमें से कौन-सा सही है?"
    assert update["pending_question"] == PendingQuestion(field="age", kind="confirm")
    assert update["contradictions"] == [contradiction.model_copy(update={"asked": True})]


async def test_the_graph_asks_until_nothing_is_missing() -> None:
    graph = build_graph(InMemorySaver(serde=checkpoint_serializer()))
    llm = agent_llm(
        [
            understood(facts={"occupation": "farmer", "state": "UP"}),
            understood(facts={"age": "64"}),
        ],
        others=_wording("Next question?"),
    )
    deps = make_deps(llm, config=AgentConfig(max_questions=1))
    config: RunnableConfig = {"configurable": {"thread_id": "q"}}

    async def turn(text: str) -> AgentState:
        result = await graph.ainvoke({"messages": [HumanMessage(text)]}, config, context=deps)
        return cast(AgentState, result)

    first = await turn("I am a farmer in UP")
    assert first["reply"] is not None
    assert first["reply"].kind == "question"
    assert first["questions_asked"] == 1
    # The budget is spent: the next turn goes on to the reply even with fields still missing.
    second = await turn("I am 64")
    assert second["questions_asked"] == 1
    assert second["profile"].age == 64
