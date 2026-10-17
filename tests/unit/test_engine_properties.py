"""Property-based tests of the three-valued evaluator (M2-T06).

Rules are random trees over a few fields of every kind; profiles hold random values or `None`.
The reference is plain two-valued evaluation of complete profiles.
"""

import operator
from collections.abc import Callable
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from sahayak.eligibility.engine import ConditionCheck, Verdict, evaluate
from sahayak.eligibility.profile import UserProfile
from sahayak.eligibility.rules import (
    AllOf,
    AnyOf,
    Condition,
    Not,
    Operator,
    RuleNode,
    SchemeRule,
    rule_issues,
)

VALUES: dict[str, st.SearchStrategy[Any]] = {
    "age": st.integers(0, 130),
    "annual_family_income_inr": st.sampled_from([0, 100000, 180000, 180001, 250000, 900000]),
    "land_holding_acres": st.sampled_from([0.0, 0.5, 2.5, 2.51, 10.0]),
    "is_bpl": st.booleans(),
    "is_govt_employee": st.booleans(),
    "gender": st.sampled_from(["male", "female", "other"]),
    "state": st.sampled_from(["MH", "UP", "DL"]),
}
COMPARE: dict[str, Callable[[Any, Any], Any]] = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}
NUMERIC = {"age", "annual_family_income_inr", "land_holding_acres"}
FIELDS = sorted(VALUES)


@st.composite
def conditions(draw: st.DrawFn) -> Condition:
    field = draw(st.sampled_from(FIELDS))
    ops: list[Operator] = ["==", "!=", "in", "not_in", "exists"]
    if field in NUMERIC:
        ops += ["<", "<=", ">", ">="]
    op = draw(st.sampled_from(ops))
    if op == "exists":
        return Condition(field=field, op=op)
    if op in ("in", "not_in"):
        return Condition(
            field=field, op=op, value=draw(st.lists(VALUES[field], min_size=1, max_size=3))
        )
    return Condition(field=field, op=op, value=draw(VALUES[field]))


def _extend(children: st.SearchStrategy[RuleNode]) -> st.SearchStrategy[RuleNode]:
    groups = st.lists(children, min_size=1, max_size=4)
    return st.one_of(
        groups.map(lambda items: AllOf(items=items)),
        groups.map(lambda items: AnyOf(items=items)),
        children.map(lambda item: Not(item=item)),
    )


trees: st.SearchStrategy[RuleNode] = st.recursive(conditions(), _extend, max_leaves=10)


@st.composite
def partial_values(draw: st.DrawFn) -> dict[str, Any]:
    return {field: draw(st.none() | strategy) for field, strategy in VALUES.items()}


def make_rule(logic: RuleNode) -> SchemeRule:
    return SchemeRule(scheme_id="s", version=1, status="approved", logic=logic)


def make_profile(values: dict[str, Any]) -> UserProfile:
    return UserProfile.model_validate({"language": "en", **values})


def reference(node: RuleNode, values: dict[str, Any]) -> bool:
    """Two-valued evaluation, for profiles where every field is known."""
    match node:
        case AllOf(items=items):
            return all(reference(item, values) for item in items)
        case AnyOf(items=items):
            return any(reference(item, values) for item in items)
        case Not(item=item):
            return not reference(item, values)
        case Condition(field=field, op="exists"):
            return True
        case Condition(field=field, op=op, value=expected):
            actual = values[field]
            if op in ("in", "not_in"):
                assert isinstance(expected, list)
                return (actual in expected) is (op == "in")
            return bool(COMPARE[op](actual, expected))
    raise AssertionError(node)


def fields_of(node: RuleNode) -> set[str]:
    match node:
        case AllOf(items=items) | AnyOf(items=items):
            return set().union(*(fields_of(item) for item in items))
        case Not(item=item):
            return fields_of(item)
        case Condition(field=field):
            return {field}
    raise AssertionError(node)


@given(trees)
def test_generated_rules_are_valid(logic: RuleNode) -> None:
    assert rule_issues(make_rule(logic)) == []


@given(trees, st.fixed_dictionaries(VALUES))
def test_complete_profiles_match_two_valued_logic(logic: RuleNode, values: dict[str, Any]) -> None:
    result = evaluate(make_rule(logic), make_profile(values))

    expected = Verdict.ELIGIBLE if reference(logic, values) else Verdict.NOT_ELIGIBLE
    assert result.verdict is expected
    assert result.missing_fields == []


@settings(max_examples=300)
@given(trees, partial_values(), st.data())
def test_definite_verdicts_survive_any_completion(
    logic: RuleNode, values: dict[str, Any], data: st.DataObject
) -> None:
    """Kleene soundness: learning more can't change ELIGIBLE or NOT_ELIGIBLE."""
    partial = evaluate(make_rule(logic), make_profile(values))
    filled = {
        field: value if value is not None else data.draw(VALUES[field], label=field)
        for field, value in values.items()
    }
    complete = evaluate(make_rule(logic), make_profile(filled))

    if partial.verdict is not Verdict.NEEDS_INFO:
        assert complete.verdict is partial.verdict


@given(trees, partial_values())
def test_result_shape_matches_the_verdict(logic: RuleNode, values: dict[str, Any]) -> None:
    result = evaluate(make_rule(logic), make_profile(values))

    if result.verdict is Verdict.ELIGIBLE:
        assert result.failed == []
        assert result.missing_fields == []
    elif result.verdict is Verdict.NOT_ELIGIBLE:
        assert result.passed == []
        assert result.missing_fields == []
        assert result.failed
    else:
        assert result.missing_fields
    assert len(result.missing_fields) == len(set(result.missing_fields))
    assert set(result.missing_fields) <= fields_of(logic)
    assert all(values[field] is None for field in result.missing_fields)


@given(trees, partial_values())
def test_reported_checks_are_true_statements(logic: RuleNode, values: dict[str, Any]) -> None:
    result = evaluate(make_rule(logic), make_profile(values))

    for check in result.passed + result.failed:
        assert values[check.condition.field] is not None
        holds = reference(check.condition, values)
        assert check.met is (holds is not check.negated)
    assert all(check.met for check in result.passed)
    assert not any(check.met for check in result.failed)


@given(trees, partial_values())
def test_not_swaps_eligible_and_not_eligible(logic: RuleNode, values: dict[str, Any]) -> None:
    plain = evaluate(make_rule(logic), make_profile(values))
    negated = evaluate(make_rule(Not(item=logic)), make_profile(values))

    swap = {
        Verdict.ELIGIBLE: Verdict.NOT_ELIGIBLE,
        Verdict.NOT_ELIGIBLE: Verdict.ELIGIBLE,
        Verdict.NEEDS_INFO: Verdict.NEEDS_INFO,
    }
    assert negated.verdict is swap[plain.verdict]
    assert negated.missing_fields == plain.missing_fields

    def flipped(checks: list[ConditionCheck]) -> list[tuple[Condition, bool]]:
        return [(check.condition, not check.negated) for check in checks]

    assert flipped(negated.failed) == [(c.condition, c.negated) for c in plain.passed]
    assert flipped(negated.passed) == [(c.condition, c.negated) for c in plain.failed]


@given(trees, partial_values(), st.data())
def test_answering_a_missing_field_only_narrows_what_is_needed(
    logic: RuleNode, values: dict[str, Any], data: st.DataObject
) -> None:
    before = evaluate(make_rule(logic), make_profile(values))
    if not before.missing_fields:
        return
    field = data.draw(st.sampled_from(before.missing_fields), label="answered")
    answered = {**values, field: data.draw(VALUES[field], label="answer")}
    after = evaluate(make_rule(logic), make_profile(answered))

    assert set(after.missing_fields) <= set(before.missing_fields) - {field}
