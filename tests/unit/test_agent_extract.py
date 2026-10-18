from typing import Any

import pytest
from langgraph.runtime import Runtime

from sahayak.agent.nodes.extract_profile import extract_profile, parse_fact
from sahayak.agent.state import (
    AgentState,
    Contradiction,
    ExtractedFact,
    PendingQuestion,
    TurnUnderstanding,
)
from sahayak.eligibility.profile import UserProfile
from tests.agent_support import NOW, make_deps, understood


@pytest.mark.parametrize(
    ("field", "text", "expected"),
    [
        ("age", "45", 45),
        ("age", " 45.0 ", 45),
        ("age", "45.5", None),
        ("age", "200", None),  # out of range
        ("age", "forty", None),
        ("annual_family_income_inr", "1,80,000", 180000),
        ("annual_family_income_inr", "₹ 2,00,000", 200000),
        ("annual_family_income_inr", "-5", None),
        ("land_holding_acres", "2.5", 2.5),
        ("is_bpl", "True", True),
        ("is_bpl", "no", False),
        ("is_bpl", "maybe", None),
        ("occupation", "Farmer", "farmer"),
        ("occupation", "doctor", None),
        ("state", "mh", "MH"),
        ("state", "Maharashtra", None),
        ("district", "Pune", "Pune"),
        ("district", "", None),
    ],
)
def test_parse_fact(field: str, text: str, expected: object) -> None:
    fact = ExtractedFact.model_validate({"field": field, "value": text})
    parsed = parse_fact(fact)
    assert parsed == expected
    assert type(parsed) is type(expected)


async def _extract(
    facts: dict[str, str] | None = None,
    *,
    profile: dict[str, Any] | None = None,
    declined: list[str] | None = None,
    **state: Any,
) -> AgentState:
    full: AgentState = {
        "profile": UserProfile.model_validate({"language": "en", **(profile or {})}),
        "understanding": TurnUnderstanding.model_validate(
            understood(facts=facts, declined=declined or ())
        ),
        "consents": ["profile"],
    }
    full.update(state)  # type: ignore[typeddict-item]
    return await extract_profile(full, Runtime(context=make_deps()))


async def test_new_facts_fill_the_profile_with_their_source() -> None:
    update = await _extract({"age": "45", "occupation": "farmer", "state": "mh"})
    profile = update["profile"]
    assert (profile.age, profile.occupation, profile.state) == (45, "farmer", "MH")
    source = profile.field_sources["age"]
    assert (source.source, source.captured_at, source.confidence) == ("self_declared", NOW, 1.0)
    assert update["contradictions"] == []


async def test_invalid_values_are_dropped() -> None:
    update = await _extract({"age": "250", "occupation": "astronaut"}, profile={"age": 30})
    assert update["profile"].age == 30
    assert update["profile"].occupation is None
    assert update["contradictions"] == []


async def test_a_different_answer_is_a_contradiction_not_an_overwrite() -> None:
    update = await _extract({"age": "54"}, profile={"age": 45})
    assert update["profile"].age == 45
    assert update["contradictions"] == [Contradiction(field="age", current=45, new=54)]


async def test_repeating_a_known_value_is_not_a_contradiction() -> None:
    update = await _extract({"is_bpl": "true"}, profile={"is_bpl": True})
    assert update["contradictions"] == []


async def test_the_confirmation_answer_wins() -> None:
    update = await _extract(
        {"age": "54"},
        profile={"age": 45},
        pending_question=PendingQuestion(field="age", kind="confirm"),
        contradictions=[Contradiction(field="age", current=45, new=54, asked=True)],
    )
    assert update["profile"].age == 54
    assert update["contradictions"] == []


async def test_an_unanswered_confirmation_keeps_the_known_value() -> None:
    update = await _extract(
        {"occupation": "farmer"},
        profile={"age": 45},
        pending_question=PendingQuestion(field="age", kind="confirm"),
        contradictions=[Contradiction(field="age", current=45, new=54, asked=True)],
    )
    assert update["profile"].age == 45
    assert update["contradictions"] == []


async def test_sensitive_facts_need_consent() -> None:
    facts = {"social_category": "obc", "disability": "true", "age": "20"}
    without = await _extract(facts)
    assert (without["profile"].social_category, without["profile"].disability) == (None, None)
    assert without["profile"].age == 20
    with_consent = await _extract(facts, consents=["profile", "sensitive"])
    assert with_consent["profile"].social_category == "obc"
    assert with_consent["profile"].disability is True


async def test_declined_fields_are_remembered_until_answered() -> None:
    update = await _extract(declined=["religion", "age"], profile={"age": 30})
    assert update["declined_fields"] == ["religion"]  # age is already known
    answered = await _extract({"religion": "hindu"}, declined_fields=["religion"])
    assert answered["declined_fields"] == ["religion"]  # no sensitive consent: still unknown
    answered = await _extract(
        {"religion": "hindu"}, declined_fields=["religion"], consents=["profile", "sensitive"]
    )
    assert answered["declined_fields"] == []
    assert answered["profile"].religion == "hindu"


async def test_no_understanding_changes_nothing() -> None:
    state: AgentState = {"profile": UserProfile(language="en"), "understanding": None}
    assert await extract_profile(state, Runtime(context=make_deps())) == {}
