"""The eligibility rule DSL (TRD §9.2, §8C): a small boolean expression tree stored as JSON.

    {"all": [{"field": "occupation", "op": "in", "value": ["farmer"]},
             {"not": {"any": [{"field": "is_income_tax_payer", "op": "==", "value": true}]}}]}

`parse_logic` checks only the JSON shape (so drafts with mistakes can still be stored and
reviewed). `rule_issues` / `validate_logic` check meaning against `UserProfile`: known fields,
operators that suit the field, values of the field's type and range. `validate_rule` does both
and is what every path that makes a rule live must call (M2-T04).
"""

import json
from datetime import date, datetime
from typing import Any, Literal, Self, assert_never

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from sahayak.eligibility.fields import PROFILE_FIELDS

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


class DocumentRequirement(BaseModel):
    """A document the applicant must hold, valid on the given date (TRD §8C; used from M10)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str = Field(min_length=1, description="document_types id, e.g. income_certificate")
    valid_on: Literal["today", "application_deadline"] = "today"


class ApplicationWindow(BaseModel):
    """When applications are accepted (TRD §8C; used from M10). An open end is None."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    opens: date | None = None
    closes: date | None = None

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.opens and self.closes and self.closes < self.opens:
            raise ValueError("closes is before opens")
        return self


class SchemeRule(BaseModel):
    """One versioned eligibility rule of a scheme, as stored and exported (TRD §9.2)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scheme_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    status: Literal["draft", "approved", "rejected", "archived"]
    logic: RuleNode
    source_refs: list[SourceRef] = Field(default_factory=list)
    notes: str | None = None
    required_documents: list[DocumentRequirement] | None = None
    application_window: ApplicationWindow | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None

    def to_json(self) -> dict[str, Any]:
        dumped: dict[str, Any] = self.model_dump(mode="json", exclude_none=True)
        return dumped


class RuleValidationError(ValueError):
    """A rule that must not be saved as live: bad JSON shape or invalid content."""

    def __init__(self, issues: list[str]) -> None:
        super().__init__("; ".join(issues))
        self.issues = issues


def parse_logic(data: object) -> RuleNode:
    """Validate the JSON shape of a rule tree. Field names and value types are not checked."""
    return _LOGIC.validate_python(data)


def dump_logic(node: RuleNode) -> dict[str, object]:
    dumped: dict[str, object] = _LOGIC.dump_python(node, mode="json", exclude_none=True)
    return dumped


def rule_json_schema() -> dict[str, Any]:
    """JSON Schema of an exported rule document, for editors and external tools."""
    schema = SchemeRule.model_json_schema(by_alias=True)
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema}


def validate_logic(node: RuleNode) -> list[str]:
    """Problems that stop a rule from being approved: unknown fields, wrong value types..."""
    issues: list[str] = []
    _check(node, issues)
    return issues


def rule_issues(rule: SchemeRule) -> list[str]:
    """Every problem with a parsed rule: its logic and its document requirements."""
    issues = validate_logic(rule.logic)
    types = [doc.type for doc in rule.required_documents or []]
    issues.extend(
        f"document {doc_type!r} is required twice"
        for doc_type in sorted(set(types))
        if types.count(doc_type) > 1
    )
    return issues


def validate_rule(data: object) -> SchemeRule:
    """Parse and fully check a rule document. Raises RuleValidationError listing every issue."""
    try:
        rule = SchemeRule.model_validate(data)
    except ValidationError as exc:
        raise RuleValidationError(
            [f"{'.'.join(map(str, e['loc'])) or 'rule'}: {e['msg']}" for e in exc.errors()]
        ) from exc
    issues = rule_issues(rule)
    if issues:
        raise RuleValidationError(issues)
    return rule


def describe(node: RuleNode, indent: int = 0) -> str:
    """Readable multi-line rendering for reviewers."""
    pad = "  " * indent
    match node:
        case Condition():
            return pad + condition_text(node)
        case AllOf(items=items):
            return "\n".join([f"{pad}ALL of:", *(describe(item, indent + 1) for item in items)])
        case AnyOf(items=items):
            return "\n".join([f"{pad}ANY of:", *(describe(item, indent + 1) for item in items)])
        case Not(item=item):
            return f"{pad}NOT:\n{describe(item, indent + 1)}"
        case _:
            assert_never(node)


def condition_text(condition: Condition) -> str:
    if condition.op == "exists":
        return f"{condition.field} exists"
    return f"{condition.field} {condition.op} {json.dumps(condition.value, ensure_ascii=False)}"


def _check(node: RuleNode, issues: list[str]) -> None:
    match node:
        case Condition():
            issues.extend(f"{condition_text(node)}: {p}" for p in _condition_problems(node))
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
        return [problem for item in value if (problem := spec.value_problem(item))]
    if value is None or isinstance(value, list):
        return [f"'{condition.op}' needs a single value"]
    if condition.op in _COMPARISONS and spec.kind not in ("int", "float"):
        return [f"'{condition.op}' needs a numeric field, {spec.name} is {spec.kind}"]
    problem = spec.value_problem(value)
    return [problem] if problem else []
