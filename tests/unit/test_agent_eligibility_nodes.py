from dataclasses import replace
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from sahayak.agent.catalogue import Catalogue
from sahayak.agent.deps import AgentConfig, AgentDeps
from sahayak.agent.nodes.check_eligibility import check_eligibility, effective_rule
from sahayak.agent.nodes.filter_results import filter_results
from sahayak.agent.nodes.shortlist_schemes import shortlist_schemes
from sahayak.agent.state import AgentState, DisplayItem, TurnUnderstanding
from sahayak.eligibility.engine import Verdict
from sahayak.eligibility.profile import UserProfile
from sahayak.eligibility.rules import AllOf
from tests.agent_support import CATALOGUE, STATE_GIRLS, make_deps, rule, understood


def _state(
    message: str = "hi", *, profile: dict[str, Any] | None = None, **extra: Any
) -> AgentState:
    state: AgentState = {
        "profile": UserProfile.model_validate({"language": "en", **(profile or {})}),
        "messages": [HumanMessage(message)],
        "understanding": TurnUnderstanding.model_validate(extra.pop("understanding", understood())),
    }
    state.update(extra)  # type: ignore[typeddict-item]
    return state


async def _shortlist(state: AgentState, deps: AgentDeps | None = None) -> AgentState:
    return await shortlist_schemes(state, Runtime(context=deps or make_deps()))


async def test_shortlist_takes_every_evaluable_scheme_in_the_state() -> None:
    update = await _shortlist(_state(profile={"state": "UP"}))
    assert update["candidate_schemes"] == ["farm-aid", "new-housing", "old-pension"]
    assert update["need"] is None


async def test_shortlist_narrows_to_the_stated_need() -> None:
    update = await _shortlist(_state(understanding=understood(query="pension")))
    assert update["candidate_schemes"] == ["old-pension"]
    assert update["need"] == "pension"


async def test_a_need_that_matches_nothing_narrows_nothing() -> None:
    update = await _shortlist(_state(understanding=understood(query="spaceship")))
    assert update["candidate_schemes"] == ["farm-aid", "mh-girls", "new-housing", "old-pension"]


async def test_named_schemes_are_evaluated_even_outside_the_state() -> None:
    update = await _shortlist(
        _state(
            "Can I get the Maharashtra Girls Scheme?",
            profile={"state": "UP"},
            understanding=understood("ask_scheme_info"),
        )
    )
    assert "mh-girls" in update["candidate_schemes"]
    assert update["named_schemes"] == ["mh-girls"]
    assert update["focus_schemes"] == ["mh-girls"]


async def test_names_read_by_the_llm_count_too() -> None:
    update = await _shortlist(_state("that first one", understanding=understood(schemes=["FAS"])))
    assert update["named_schemes"] == ["farm-aid"]


async def test_discontinued_schemes_are_never_candidates() -> None:
    update = await _shortlist(_state("Tell me about the Closed Scheme"))
    assert update["named_schemes"] == ["closed-scheme"]
    assert "closed-scheme" not in update["candidate_schemes"]


async def test_need_and_focus_carry_over_to_answers_and_reset_on_a_general_request() -> None:
    carried = await _shortlist(
        _state(
            "45",
            need="pension",
            focus_schemes=["old-pension"],
            understanding=understood(facts={"age": "45"}),
        )
    )
    assert (carried["need"], carried["focus_schemes"]) == ("pension", ["old-pension"])
    reset = await _shortlist(
        _state("Which schemes can I get?", need="pension", focus_schemes=["old-pension"])
    )
    assert (reset["need"], reset["focus_schemes"]) == (None, [])


async def test_selecting_a_scheme_by_name() -> None:
    update = await _shortlist(_state("I want Farm Aid", understanding=understood("select_scheme")))
    assert update["selected_scheme"] == "farm-aid"


def test_state_schemes_are_restricted_to_their_state() -> None:
    rule = effective_rule(STATE_GIRLS)
    assert rule is not None
    assert isinstance(rule.logic, AllOf)
    assert rule.logic.items[0].model_dump() == {"field": "state", "op": "==", "value": "MH"}


async def _check(profile: dict[str, Any], candidates: list[str]) -> AgentState:
    state = _state(profile=profile, candidate_schemes=candidates)
    return await check_eligibility(state, Runtime(context=make_deps()))


async def test_check_eligibility_runs_the_engine_on_candidates() -> None:
    update = await _check(
        {"age": 65, "occupation": "farmer", "state": "UP", "gender": "female"},
        ["farm-aid", "old-pension", "mh-girls", "unknown-scheme"],
    )
    verdicts = {sid: r.verdict for sid, r in update["eligibility"].items()}
    assert verdicts == {
        "farm-aid": Verdict.NEEDS_INFO,  # land unknown
        "old-pension": Verdict.ELIGIBLE,
        "mh-girls": Verdict.NOT_ELIGIBLE,  # female, but not in MH
    }
    assert update["eligibility"]["farm-aid"].missing_fields == ["land_holding_acres"]


async def test_a_malformed_draft_is_skipped() -> None:
    bad_rule = rule(
        "new-housing", {"all": [{"field": "no_such_field", "op": "==", "value": 1}]}, status="draft"
    )
    catalogue = Catalogue.of(
        [replace(CATALOGUE.schemes["new-housing"], rule=bad_rule), CATALOGUE.schemes["farm-aid"]]
    )
    state = _state(candidate_schemes=["new-housing", "farm-aid"])
    update = await check_eligibility(state, Runtime(context=make_deps(catalogue=catalogue)))
    assert list(update["eligibility"]) == ["farm-aid"]


async def _filter(profile: dict[str, Any], *, show_provisional: bool = True) -> AgentState:
    deps = make_deps(config=AgentConfig(show_provisional=show_provisional))
    candidates = sorted(CATALOGUE.schemes)
    checked = await check_eligibility(
        _state(profile=profile, candidate_schemes=candidates), Runtime(context=deps)
    )
    return await filter_results(
        _state(profile=profile, eligibility=checked["eligibility"]), Runtime(context=deps)
    )


async def test_filter_shows_only_eligible_schemes_in_their_group() -> None:
    profile = {
        "age": 70,
        "occupation": "farmer",
        "land_holding_acres": 0,
        "residence": "rural",
        "state": "MH",
        "gender": "male",
    }
    results = (await _filter(profile))["display_results"]
    assert [i.scheme_id for i in results.ready] == ["old-pension"]
    # farm-aid: NOT_ELIGIBLE (no land); mh-girls: NOT_ELIGIBLE; closed-scheme: discontinued
    assert results.provisional == [
        DisplayItem(
            scheme_id="new-housing",
            rule_version=1,
            provisional=True,
            reasons=['residence == "rural"'],
        )
    ]
    assert results.ready[0].reasons == ["age >= 60"]


async def test_provisional_schemes_can_be_hidden() -> None:
    results = (await _filter({"residence": "rural"}, show_provisional=False))["display_results"]
    assert results.scheme_ids() == []


async def test_needs_info_is_never_shown() -> None:
    results = (await _filter({}))["display_results"]
    assert results.scheme_ids() == []
