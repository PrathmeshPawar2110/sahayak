from datetime import date
from typing import get_args

import pytest
from pydantic import ValidationError

from sahayak.eligibility.fields import PROFILE_FIELDS, ProfileFieldName
from sahayak.eligibility.rules import (
    AllOf,
    ApplicationWindow,
    Condition,
    DocumentRequirement,
    Not,
    RuleValidationError,
    SchemeRule,
    SourceRef,
    describe,
    dump_logic,
    parse_logic,
    rule_issues,
    rule_json_schema,
    validate_logic,
    validate_rule,
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


def rule_document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "scheme_id": "pm-kisan",
        "version": 3,
        "status": "approved",
        "logic": PM_KISAN,
        "source_refs": [{"url": "https://pmkisan.gov.in/guidelines.pdf", "section": "Exclusions"}],
        "notes": "Institutional land holders excluded.",
    }
    return {**document, **overrides}


def test_the_trd_rule_document_validates() -> None:
    rule = validate_rule(rule_document())

    assert rule.version == 3
    assert rule.source_refs == [
        SourceRef(url="https://pmkisan.gov.in/guidelines.pdf", section="Exclusions")
    ]
    assert rule.required_documents is None
    assert rule.to_json() == rule_document()


def test_documents_and_application_window() -> None:
    rule = validate_rule(
        rule_document(
            required_documents=[
                {"type": "income_certificate", "valid_on": "application_deadline"},
                {"type": "caste_certificate"},
            ],
            application_window={"opens": "2026-07-01", "closes": "2026-11-30"},
        )
    )

    assert rule.required_documents == [
        DocumentRequirement(type="income_certificate", valid_on="application_deadline"),
        DocumentRequirement(type="caste_certificate", valid_on="today"),
    ]
    assert rule.application_window == ApplicationWindow(
        opens=date(2026, 7, 1), closes=date(2026, 11, 30)
    )
    assert SchemeRule.model_validate(rule.to_json()) == rule


@pytest.mark.parametrize(
    ("overrides", "issue"),
    [
        ({"logic": {"field": "caste", "op": "==", "value": "x"}}, "unknown field 'caste'"),
        ({"logic": {"field": "age", "op": "~", "value": 1}}, "logic"),
        ({"version": 0}, "version: Input should be greater than or equal to 1"),
        ({"status": "live"}, "status"),
        ({"scheme_id": ""}, "scheme_id"),
        ({"surprise": 1}, "surprise: Extra inputs are not permitted"),
        (
            {"application_window": {"opens": "2026-12-01", "closes": "2026-11-30"}},
            "closes is before opens",
        ),
        ({"required_documents": [{"type": "x", "valid_on": "tomorrow"}]}, "valid_on"),
        (
            {
                "required_documents": [
                    {"type": "income_certificate"},
                    {"type": "income_certificate"},
                ]
            },
            "document 'income_certificate' is required twice",
        ),
        ({"logic": {"field": "state", "op": "==", "value": "mh"}}, "write it as 'MH'"),
        ({"logic": {"field": "age", "op": "<", "value": 200}}, "less than or equal to 130"),
    ],
)
def test_validate_rule_lists_every_issue(overrides: dict[str, object], issue: str) -> None:
    with pytest.raises(RuleValidationError) as caught:
        validate_rule(rule_document(**overrides))

    assert any(issue in found for found in caught.value.issues), caught.value.issues


def test_rule_issues_is_empty_for_a_valid_rule() -> None:
    assert rule_issues(SchemeRule.model_validate(rule_document())) == []


def test_json_schema_export_describes_the_document() -> None:
    schema = rule_json_schema()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert set(schema["required"]) == {"scheme_id", "version", "status", "logic"}
    assert {"Condition", "AllOf", "AnyOf", "Not", "DocumentRequirement"} <= set(schema["$defs"])
    assert schema["$defs"]["Condition"]["properties"]["op"]["enum"] == [
        "==",
        "!=",
        "<",
        "<=",
        ">",
        ">=",
        "in",
        "not_in",
        "exists",
    ]
    assert "all" in schema["$defs"]["AllOf"]["properties"]
