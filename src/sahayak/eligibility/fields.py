"""Profile fields that eligibility rules may test, derived from `UserProfile` (TRD §9.1).

`UserProfile` is the single source of truth for names, types, choices, constraints and
descriptions; this module flattens it into a catalogue the rule validator and the rule-extraction
prompt can use. `ProfileFieldName` lists the same names statically so they can be offered to the
LLM as an enum (a test keeps the two equal).
"""

from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, get_args, get_origin

from pydantic import TypeAdapter, ValidationError
from pydantic.fields import FieldInfo

from sahayak.eligibility.profile import UserProfile

FieldKind = Literal["int", "float", "bool", "str", "enum"]

ProfileFieldName = Literal[
    "state",
    "district",
    "age",
    "gender",
    "annual_family_income_inr",
    "social_category",
    "caste_name",
    "caste_validity_verified",
    "is_non_creamy_layer",
    "religion",
    "is_minority",
    "domicile_state",
    "disability_percent",
    "current_course",
    "marks_percent",
    "occupation",
    "land_holding_acres",
    "is_bpl",
    "has_ration_card",
    "disability",
    "is_income_tax_payer",
    "is_govt_employee",
    "marital_status",
    "num_children",
    "education_level",
    "residence",
]

# `UserProfile` fields that describe the conversation, not the citizen.
NON_ELIGIBILITY_FIELDS = frozenset({"language", "field_sources"})

_KINDS: dict[type, FieldKind] = {int: "int", float: "float", bool: "bool", str: "str"}


@dataclass(frozen=True)
class ProfileField:
    name: str
    kind: FieldKind
    description: str
    choices: tuple[str, ...] = ()
    sensitive: bool = False  # TRD §8B: optional, consent-gated, never inferred
    # Validates one value against the field's type and constraints (range, pattern...).
    adapter: TypeAdapter[Any] | None = field(default=None, compare=False, repr=False)

    def value_problem(self, value: object) -> str | None:
        """Why `value` can't be a value of this field, or None if it can."""
        if self.kind == "enum":
            if isinstance(value, str) and value in self.choices:
                return None
            return f"{value!r} is not one of: {', '.join(self.choices)}"
        is_number = isinstance(value, int | float) and not isinstance(value, bool)
        type_ok = {
            "bool": isinstance(value, bool),
            "int": is_number and isinstance(value, int),
            "float": is_number,
            "str": isinstance(value, str),
        }[self.kind]
        if not type_ok:
            return f"{value!r} is not a valid {self.kind} for {self.name}"
        if self.adapter is None:
            return None
        try:
            normalised = self.adapter.validate_python(value)
        except ValidationError as exc:
            reason = exc.errors()[0]["msg"]
            return f"{value!r} is not a valid {self.name}: {reason[0].lower()}{reason[1:]}"
        if normalised != value:
            return f"{value!r} is not a valid {self.name}: write it as {normalised!r}"
        return None


def _profile_field(name: str, info: FieldInfo) -> ProfileField:
    (inner,) = (arg for arg in get_args(info.annotation) if arg is not type(None))
    base = get_args(inner)[0] if get_origin(inner) is Annotated else inner
    extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
    if get_origin(base) is Literal:
        kind: FieldKind = "enum"
        choices = tuple(str(choice) for choice in get_args(base))
    else:
        kind, choices = _KINDS[base], ()
    return ProfileField(
        name=name,
        kind=kind,
        description=info.description or name,
        choices=choices,
        sensitive=bool(extra.get("sensitive", False)),
        adapter=None if kind == "enum" else TypeAdapter(_with_metadata(inner, info)),
    )


def _with_metadata(annotation: Any, info: FieldInfo) -> Any:
    # Field-level constraints such as `ge=0` live in `metadata`, not in the annotation.
    return Annotated[annotation, *info.metadata] if info.metadata else annotation


PROFILE_FIELDS: dict[str, ProfileField] = {
    name: _profile_field(name, info)
    for name, info in UserProfile.model_fields.items()
    if name not in NON_ELIGIBILITY_FIELDS
}
