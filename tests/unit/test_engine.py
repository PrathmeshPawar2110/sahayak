import pytest

from sahayak.eligibility.engine import (
    ConditionCheck,
    Truth,
    Verdict,
    evaluate,
    evaluate_all,
    evaluate_logic,
)
from sahayak.eligibility.profile import UserProfile
from sahayak.eligibility.rules import Condition, SchemeRule, parse_logic, validate_rule

# TRD §9.2 example, as the eval reference reads PM-KISAN.
PM_KISAN = validate_rule(
    {
        "scheme_id": "pm-kisan",
        "version": 3,
        "status": "approved",
        "logic": {
            "all": [
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
        },
    }
)


def profile(**fields: object) -> UserProfile:
    return UserProfile.model_validate({"language": "en", **fields})


def rule(logic: object, scheme_id: str = "s") -> SchemeRule:
    return validate_rule(
        {"scheme_id": scheme_id, "version": 1, "status": "approved", "logic": logic}
    )


def texts(checks: list[ConditionCheck]) -> list[str]:
    return [check.text() for check in checks]


def test_eligible_lists_every_condition_that_decided_it() -> None:
    result = evaluate(
        PM_KISAN,
        profile(land_holding_acres=2.0, is_income_tax_payer=False, is_govt_employee=False),
    )

    assert result.verdict is Verdict.ELIGIBLE
    assert (result.scheme_id, result.rule_version) == ("pm-kisan", 3)
    assert texts(result.passed) == [
        "land_holding_acres > 0",
        "NOT (is_income_tax_payer == true)",
        "NOT (is_govt_employee == true)",
    ]
    assert result.failed == []
    assert result.missing_fields == []


def test_a_failed_condition_rules_out_without_asking_more() -> None:
    result = evaluate(PM_KISAN, profile(land_holding_acres=0.0))

    assert result.verdict is Verdict.NOT_ELIGIBLE
    assert texts(result.failed) == ["land_holding_acres > 0"]
    assert result.passed == []
    assert result.missing_fields == []  # tax and employment no longer matter


def test_an_exclusion_rules_out_through_not() -> None:
    result = evaluate(PM_KISAN, profile(is_govt_employee=True))

    assert result.verdict is Verdict.NOT_ELIGIBLE
    assert texts(result.failed) == ["NOT (is_govt_employee == true)"]
    assert result.failed[0].negated
    assert not result.failed[0].met
    assert result.missing_fields == []


def test_unknown_fields_give_needs_info_with_what_is_known_so_far() -> None:
    result = evaluate(PM_KISAN, profile(land_holding_acres=1.5, is_income_tax_payer=False))

    assert result.verdict is Verdict.NEEDS_INFO
    assert texts(result.passed) == ["land_holding_acres > 0", "NOT (is_income_tax_payer == true)"]
    assert result.failed == []
    assert result.missing_fields == ["is_govt_employee"]


def test_empty_profile_needs_every_field_once() -> None:
    logic = {
        "all": [
            {"field": "age", "op": ">=", "value": 18},
            {"field": "age", "op": "<=", "value": 40},
            {"field": "is_income_tax_payer", "op": "==", "value": False},
        ]
    }

    result = evaluate(rule(logic), profile())

    assert result.verdict is Verdict.NEEDS_INFO
    assert result.missing_fields == ["age", "is_income_tax_payer"]


def test_any_is_decided_by_one_true_item() -> None:
    logic = {
        "any": [
            {"field": "is_bpl", "op": "==", "value": True},
            {"field": "has_ration_card", "op": "in", "value": ["aay", "phh"]},
        ]
    }

    eligible = evaluate(rule(logic), profile(has_ration_card="aay"))
    unknown = evaluate(rule(logic), profile(has_ration_card="apl"))
    ruled_out = evaluate(rule(logic), profile(has_ration_card="apl", is_bpl=False))

    assert (eligible.verdict, texts(eligible.passed), eligible.missing_fields) == (
        Verdict.ELIGIBLE,
        ['has_ration_card in ["aay", "phh"]'],
        [],
    )
    assert (unknown.verdict, texts(unknown.failed), unknown.missing_fields) == (
        Verdict.NEEDS_INFO,
        ['has_ration_card in ["aay", "phh"]'],
        ["is_bpl"],
    )
    assert ruled_out.verdict is Verdict.NOT_ELIGIBLE
    assert len(ruled_out.failed) == 2


@pytest.mark.parametrize(
    ("condition", "value", "holds"),
    [
        ({"field": "age", "op": "==", "value": 40}, 40, True),
        ({"field": "age", "op": "!=", "value": 40}, 40, False),
        ({"field": "age", "op": "<", "value": 40}, 40, False),
        ({"field": "age", "op": "<=", "value": 40}, 40, True),
        ({"field": "age", "op": ">", "value": 40}, 41, True),
        ({"field": "age", "op": ">=", "value": 40}, 39, False),
        ({"field": "age", "op": "in", "value": [30, 40]}, 40, True),
        ({"field": "age", "op": "not_in", "value": [30, 40]}, 40, False),
        ({"field": "age", "op": "exists"}, 0, True),
        ({"field": "land_holding_acres", "op": ">", "value": 0}, 0.01, True),
        ({"field": "land_holding_acres", "op": "<=", "value": 2.5}, 2.5, True),
        ({"field": "is_bpl", "op": "==", "value": False}, False, True),
        ({"field": "state", "op": "not_in", "value": ["DL", "WB"]}, "MH", True),
    ],
)
def test_operators(condition: dict[str, object], value: object, holds: bool) -> None:
    outcome = evaluate_logic(parse_logic(condition), profile(**{str(condition["field"]): value}))

    assert outcome.truth is (Truth.TRUE if holds else Truth.FALSE)


def test_exists_asks_for_the_field_instead_of_ruling_out() -> None:
    result = evaluate(rule({"field": "district", "op": "exists"}), profile())
    negated = evaluate(rule({"not": {"field": "district", "op": "exists"}}), profile())

    assert (result.verdict, result.missing_fields) == (Verdict.NEEDS_INFO, ["district"])
    assert (negated.verdict, negated.missing_fields) == (Verdict.NEEDS_INFO, ["district"])
    assert evaluate(
        rule({"field": "district", "op": "exists"}), profile(district="Pune")
    ).verdict is (Verdict.ELIGIBLE)


def test_bool_values_never_equal_numbers() -> None:
    # Hand-built (unvalidated) nodes: the engine still keeps True apart from 1.
    node = Condition(field="age", op="==", value=True)

    assert evaluate_logic(node, profile(age=1)).truth is Truth.FALSE
    assert evaluate_logic(Condition(field="age", op="in", value=[True]), profile(age=1)).truth is (
        Truth.FALSE
    )


@pytest.mark.parametrize(
    "node",
    [
        Condition(field="age", op="in", value=30),
        Condition(field="age", op="<", value="30"),
        Condition(field="gender", op="<", value=3),
        Condition(field="age", op=">=", value=True),
    ],
)
def test_unvalidated_type_errors_are_reported(node: Condition) -> None:
    with pytest.raises(ValueError, match=r"needs numbers|expected a list"):
        evaluate_logic(node, profile(age=30, gender="male"))


def test_double_negation_is_identity() -> None:
    inner = {"field": "is_bpl", "op": "==", "value": True}

    for value in (True, False, None):
        plain = evaluate(rule(inner), profile(is_bpl=value))
        double = evaluate(rule({"not": {"not": inner}}), profile(is_bpl=value))
        assert double.verdict is plain.verdict
        assert double.missing_fields == plain.missing_fields


def test_evaluate_all_keys_results_by_scheme() -> None:
    results = evaluate_all(
        [PM_KISAN, rule({"field": "age", "op": ">=", "value": 18}, scheme_id="apy")],
        profile(age=30),
    )

    assert {k: r.verdict for k, r in results.items()} == {
        "pm-kisan": Verdict.NEEDS_INFO,
        "apy": Verdict.ELIGIBLE,
    }


def test_results_serialise_for_agent_state() -> None:
    result = evaluate(PM_KISAN, profile(is_govt_employee=True))

    dumped = result.model_dump(mode="json")

    assert dumped["verdict"] == "NOT_ELIGIBLE"
    assert dumped["failed"][0]["condition"] == {
        "field": "is_govt_employee",
        "op": "==",
        "value": True,
    }
    assert type(result).model_validate(dumped) == result
