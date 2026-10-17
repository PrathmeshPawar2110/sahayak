"""Deterministic, three-valued evaluation of eligibility rules (TRD §6, §9.2, FR-6).

Every node evaluates to TRUE, FALSE or UNKNOWN (Kleene logic). A condition is UNKNOWN when its
field is `None` in the profile, which means "not known yet". `exists` is TRUE when the field
has a value and UNKNOWN otherwise, so it asks for the field instead of ruling the scheme out.

    all: FALSE if any item is FALSE, else UNKNOWN if any is UNKNOWN, else TRUE
    any: TRUE if any item is TRUE, else UNKNOWN if any is UNKNOWN, else FALSE
    not: swaps TRUE and FALSE; UNKNOWN stays UNKNOWN

A definite result carries only what decided it. A FALSE `all` reports its FALSE items and
ignores the unknown ones, so a scheme already ruled out never asks for more information, and an
ELIGIBLE result never lists a failed condition. A NEEDS_INFO result reports what is known so far
and, as missing fields, the unknown fields inside the parts of the rule that are still undecided.

The LLM never calls into this module's decisions; it only explains them.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import assert_never

from pydantic import BaseModel, ConfigDict

from sahayak.eligibility.profile import UserProfile
from sahayak.eligibility.rules import (
    AllOf,
    AnyOf,
    Condition,
    Not,
    RuleNode,
    Scalar,
    SchemeRule,
    condition_text,
)


class Verdict(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    NEEDS_INFO = "NEEDS_INFO"


class Truth(Enum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"

    def __invert__(self) -> "Truth":
        return {Truth.TRUE: Truth.FALSE, Truth.FALSE: Truth.TRUE}.get(self, Truth.UNKNOWN)


_VERDICTS = {
    Truth.TRUE: Verdict.ELIGIBLE,
    Truth.FALSE: Verdict.NOT_ELIGIBLE,
    Truth.UNKNOWN: Verdict.NEEDS_INFO,
}


class ConditionCheck(BaseModel):
    """One condition as it applied to the profile.

    `negated` is true when the condition sits under an odd number of `not`s: then the
    requirement is that it does *not* hold. `met` says whether that requirement is met.
    """

    model_config = ConfigDict(frozen=True)

    condition: Condition
    negated: bool = False
    met: bool

    def text(self) -> str:
        body = condition_text(self.condition)
        return f"NOT ({body})" if self.negated else body


class EligibilityResult(BaseModel):
    """The engine's verdict for one scheme, with the conditions that decided it (FR-6)."""

    model_config = ConfigDict(frozen=True)

    scheme_id: str
    rule_version: int
    verdict: Verdict
    passed: list[ConditionCheck]
    failed: list[ConditionCheck]
    missing_fields: list[str]


@dataclass(frozen=True)
class LogicOutcome:
    """How a (sub)tree evaluated: its truth, the checks that decided it, and missing fields."""

    truth: Truth
    checks: tuple[ConditionCheck, ...] = ()
    missing: tuple[str, ...] = ()


def evaluate(rule: SchemeRule, profile: UserProfile) -> EligibilityResult:
    """Evaluate one rule. The rule must have passed `validate_rule` (its values fit the fields)."""
    outcome = evaluate_logic(rule.logic, profile)
    return EligibilityResult(
        scheme_id=rule.scheme_id,
        rule_version=rule.version,
        verdict=_VERDICTS[outcome.truth],
        passed=[check for check in outcome.checks if check.met],
        failed=[check for check in outcome.checks if not check.met],
        missing_fields=list(dict.fromkeys(outcome.missing)),
    )


def evaluate_all(rules: Iterable[SchemeRule], profile: UserProfile) -> dict[str, EligibilityResult]:
    return {rule.scheme_id: evaluate(rule, profile) for rule in rules}


def evaluate_logic(node: RuleNode, profile: UserProfile) -> LogicOutcome:
    match node:
        case Condition():
            return _condition(node, profile)
        case AllOf(items=items):
            return _combine([evaluate_logic(item, profile) for item in items], decisive=Truth.FALSE)
        case AnyOf(items=items):
            return _combine([evaluate_logic(item, profile) for item in items], decisive=Truth.TRUE)
        case Not(item=item):
            inner = evaluate_logic(item, profile)
            return LogicOutcome(
                ~inner.truth,
                tuple(
                    check.model_copy(update={"negated": not check.negated, "met": not check.met})
                    for check in inner.checks
                ),
                inner.missing,
            )
        case _:
            assert_never(node)


def _combine(outcomes: list[LogicOutcome], *, decisive: Truth) -> LogicOutcome:
    """`all` (decisive FALSE) or `any` (decisive TRUE) over already evaluated items."""
    decided = [o for o in outcomes if o.truth is decisive]
    if decided:  # short-circuit: only the decisive items explain the result
        return LogicOutcome(decisive, _checks(decided))
    unknown = [o for o in outcomes if o.truth is Truth.UNKNOWN]
    if unknown:  # what is known so far, plus every field that could still decide it
        return LogicOutcome(
            Truth.UNKNOWN, _checks(outcomes), tuple(f for o in unknown for f in o.missing)
        )
    return LogicOutcome(~decisive, _checks(outcomes))


def _checks(outcomes: Iterable[LogicOutcome]) -> tuple[ConditionCheck, ...]:
    return tuple(check for outcome in outcomes for check in outcome.checks)


def _condition(condition: Condition, profile: UserProfile) -> LogicOutcome:
    actual = getattr(profile, condition.field)
    if actual is None:
        return LogicOutcome(Truth.UNKNOWN, missing=(condition.field,))
    holds = _holds(condition, actual)
    return LogicOutcome(
        Truth.TRUE if holds else Truth.FALSE, (ConditionCheck(condition=condition, met=holds),)
    )


def _holds(condition: Condition, actual: Scalar) -> bool:
    expected = condition.value
    match condition.op:
        case "exists":
            return True
        case "==":
            return _same(actual, expected)
        case "!=":
            return not _same(actual, expected)
        case "in":
            return any(_same(actual, item) for item in _as_list(expected))
        case "not_in":
            return not any(_same(actual, item) for item in _as_list(expected))
        case "<" | "<=" | ">" | ">=":
            return _compare(condition.op, actual, expected)
        case _:
            assert_never(condition.op)


def _same(actual: Scalar, expected: object) -> bool:
    # bool is an int in Python: keep True from equalling 1.
    return isinstance(actual, bool) is isinstance(expected, bool) and actual == expected


def _as_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"expected a list of values, got {value!r}")
    return list(value)


def _compare(op: str, actual: Scalar, expected: object) -> bool:
    if (
        isinstance(actual, bool | str)
        or isinstance(expected, bool)
        or not isinstance(expected, int | float)
    ):
        raise ValueError(f"{op} needs numbers, got {actual!r} and {expected!r}")
    return {"<": actual < expected, "<=": actual <= expected, ">": actual > expected}.get(
        op, actual >= expected
    )
