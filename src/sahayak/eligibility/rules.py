"""The eligibility rule DSL (TRD §9.2): a small boolean expression tree stored as JSON.

    {"all": [{"field": "occupation", "op": "in", "value": ["farmer"]},
             {"not": {"any": [{"field": "is_income_tax_payer", "op": "==", "value": true}]}}]}

This is the minimal version M1 needs to store and review extracted drafts. M2-T01 adds the
documents and application-window parts and the JSON Schema export; M2-T03 evaluates it.
"""

import json
from typing import Literal, assert_never

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from sahayak.eligibility.fields import PROFILE_FIELDS, ProfileField

Operator = Literal["==", "!=", "<", "<=", ">", ">=", "in", "not_in", "exists"]
Scalar = bool | int | float | str

_COMPARISONS = ("<", "<=", ">", ">=")


class _Node(BaseModel):
    # The JSON keys `all` / `any` / `not` are Python keywords or builtins, hence the aliases.
    model_config = ConfigDict(
        extra="forbid", frozen=True, validate_by_name=True, serialize_by_alias=True
    )


class Condition(_Node):
    field: str
    op: Operator
    value: Scalar | list[Scalar] | None = None  # omitted for `exists`


class AllOf(_Node):
    items: list["RuleNode"] = Field(alias="all")


class AnyOf(_Node):
    items: list["RuleNode"] = Field(alias="any")


class Not(_Node):
    item: "RuleNode" = Field(alias="not")


RuleNode = Condition | AllOf | AnyOf | Not

for _model in (AllOf, AnyOf, Not):
    _model.model_rebuild()

_LOGIC: TypeAdapter[RuleNode] = TypeAdapter(RuleNode)


class SourceRef(BaseModel):
    """Where in the source documents a rule comes from, for review and citations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str
    section: str


def parse_logic(data: object) -> RuleNode:
    """Validate the JSON shape of a rule tree. Field names and value types are not checked."""
    return _LOGIC.validate_python(data)


def dump_logic(node: RuleNode) -> dict[str, object]:
    dumped: dict[str, object] = _LOGIC.dump_python(node, mode="json", exclude_none=True)
    return dumped


def validate_logic(node: RuleNode) -> list[str]:
    """Problems that stop a rule from being approved: unknown fields, wrong value types..."""
    issues: list[str] = []
    _check(node, issues)
    return issues


def describe(node: RuleNode, indent: int = 0) -> str:
    """Readable multi-line rendering for reviewers."""
    pad = "  " * indent
    match node:
        case Condition():
            return pad + _condition_text(node)
        case AllOf(items=items):
            return "\n".join([f"{pad}ALL of:", *(describe(item, indent + 1) for item in items)])
        case AnyOf(items=items):
            return "\n".join([f"{pad}ANY of:", *(describe(item, indent + 1) for item in items)])
        case Not(item=item):
            return f"{pad}NOT:\n{describe(item, indent + 1)}"
        case _:
            assert_never(node)


def _check(node: RuleNode, issues: list[str]) -> None:
    match node:
        case Condition():
            issues.extend(f"{_condition_text(node)}: {p}" for p in _condition_problems(node))
        case AllOf(items=items) | AnyOf(items=items):
            if not items:
                issues.append(f"empty '{'all' if isinstance(node, AllOf) else 'any'}' group")
            for item in items:
                _check(item, issues)
        case Not(item=item):
            _check(item, issues)
        case _:
            assert_never(node)


def _condition_problems(condition: Condition) -> list[str]:
    spec = PROFILE_FIELDS.get(condition.field)
    if spec is None:
        return [f"unknown field {condition.field!r}"]
    value = condition.value
    if condition.op == "exists":
        return [] if value is None else ["'exists' takes no value"]
    if condition.op in ("in", "not_in"):
        if not isinstance(value, list) or not value:
            return [f"'{condition.op}' needs a non-empty list"]
        return [problem for item in value if (problem := _value_problem(spec, item))]
    if value is None or isinstance(value, list):
        return [f"'{condition.op}' needs a single value"]
    if condition.op in _COMPARISONS and spec.kind not in ("int", "float"):
        return [f"'{condition.op}' needs a numeric field, {spec.name} is {spec.kind}"]
    problem = _value_problem(spec, value)
    return [problem] if problem else []


def _value_problem(spec: ProfileField, value: Scalar) -> str | None:
    if spec.kind == "enum":
        if isinstance(value, str) and value in spec.choices:
            return None
        return f"{value!r} is not one of: {', '.join(spec.choices)}"
    is_number = isinstance(value, int | float) and not isinstance(value, bool)
    ok = {
        "bool": isinstance(value, bool),
        "int": is_number and isinstance(value, int),
        "float": is_number,
        "str": isinstance(value, str),
    }[spec.kind]
    return None if ok else f"{value!r} is not a valid {spec.kind} for {spec.name}"


def _condition_text(condition: Condition) -> str:
    if condition.op == "exists":
        return f"{condition.field} exists"
    return f"{condition.field} {condition.op} {json.dumps(condition.value, ensure_ascii=False)}"
