from typing import get_args

import pytest
from pydantic import ValidationError

from sahayak.eligibility.fields import PROFILE_FIELDS, ProfileFieldName
from sahayak.eligibility.rules import (
    AllOf,
    Condition,
    Not,
    describe,
    dump_logic,
    parse_logic,
    validate_logic,
)

# The TRD §9.2 example, with an exclusion group added.
PM_KISAN = {
    "all": [
        {"field": "occupation", "op": "in", "value": ["farmer"]},
        {"field": "land_holding_acres", "op": ">", "value": 0},
        {
            "not": {
                "any": [
                    {"field": "is_income_tax_payer", "op": "==", "value": True},
                    {"field": "is_govt_employee", "op": "==", "value": True},
                ]
            }
        },
    ]
}


def test_field_names_match_the_catalogue() -> None:
    assert list(get_args(ProfileFieldName)) == list(PROFILE_FIELDS)


def test_parse_and_dump_round_trip() -> None:
    node = parse_logic(PM_KISAN)

    assert isinstance(node, AllOf)
    assert isinstance(node.items[2], Not)
    assert dump_logic(node) == PM_KISAN
    assert validate_logic(node) == []


def test_values_keep_their_json_types() -> None:
    node = parse_logic({"any": [{"field": "age", "op": ">=", "value": 60}]})

    assert dump_logic(node) == {"any": [{"field": "age", "op": ">=", "value": 60}]}
    assert (
        type(dump_logic(parse_logic({"field": "is_bpl", "op": "==", "value": True}))["value"])
        is bool
    )


def test_exists_has_no_value_key() -> None:
    assert dump_logic(Condition(field="district", op="exists")) == {
        "field": "district",
        "op": "exists",
    }


@pytest.mark.parametrize(
    "data",
    [
        {"all": [{"field": "age", "op": "~", "value": 1}]},  # unknown operator
        {"every": []},  # unknown combinator
        {"field": "age", "op": ">=", "value": 1, "extra": True},
        {"not": [{"field": "age", "op": ">=", "value": 1}]},  # `not` takes one node
    ],
)
def test_malformed_trees_are_rejected(data: object) -> None:
    with pytest.raises(ValidationError):
        parse_logic(data)


@pytest.mark.parametrize(
    ("condition", "problem"),
    [
        ({"field": "caste", "op": "==", "value": "x"}, "unknown field 'caste'"),
        ({"field": "is_bpl", "op": ">", "value": 0}, "needs a numeric field"),
        ({"field": "occupation", "op": "in", "value": "farmer"}, "needs a non-empty list"),
        ({"field": "occupation", "op": "in", "value": []}, "needs a non-empty list"),
        ({"field": "occupation", "op": "==", "value": "fisher"}, "is not one of"),
        ({"field": "age", "op": ">=", "value": 18.5}, "is not a valid int"),
        ({"field": "age", "op": "==", "value": True}, "is not a valid int"),
        ({"field": "is_bpl", "op": "==", "value": "yes"}, "is not a valid bool"),
        ({"field": "age", "op": "==", "value": [1, 2]}, "needs a single value"),
        ({"field": "age", "op": "=="}, "needs a single value"),
        ({"field": "district", "op": "exists", "value": "Pune"}, "takes no value"),
    ],
)
def test_validation_problems(condition: dict[str, object], problem: str) -> None:
    issues = validate_logic(parse_logic({"all": [condition]}))

    assert len(issues) == 1
    assert problem in issues[0]


def test_numbers_are_valid_for_float_fields_and_empty_groups_are_flagged() -> None:
    assert validate_logic(parse_logic({"field": "land_holding_acres", "op": ">", "value": 0})) == []
    assert validate_logic(parse_logic({"all": [{"any": []}]})) == ["empty 'any' group"]


def test_describe_is_readable() -> None:
    assert describe(parse_logic(PM_KISAN)) == (
        "ALL of:\n"
        '  occupation in ["farmer"]\n'
        "  land_holding_acres > 0\n"
        "  NOT:\n"
        "    ANY of:\n"
        "      is_income_tax_payer == true\n"
        "      is_govt_employee == true"
    )
