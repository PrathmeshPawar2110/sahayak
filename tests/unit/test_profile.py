from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from sahayak.eligibility.fields import NON_ELIGIBILITY_FIELDS, PROFILE_FIELDS
from sahayak.eligibility.profile import FieldSource, UserProfile


def test_only_language_is_required() -> None:
    profile = UserProfile(language="mr")

    assert all(getattr(profile, name) is None for name in PROFILE_FIELDS)
    assert profile.field_sources == {}


def test_state_codes_are_normalised() -> None:
    profile = UserProfile(language="en", state=" mh ", domicile_state="up")

    assert (profile.state, profile.domicile_state) == ("MH", "UP")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("state", "Maharashtra"),
        ("age", -1),
        ("age", 131),
        ("annual_family_income_inr", -5),
        ("disability_percent", 101),
        ("marks_percent", 100.5),
        ("land_holding_acres", -0.5),
        ("num_children", -1),
        ("occupation", "doctor"),
        ("social_category", "OBC"),
        ("has_ration_card", "yellow"),
    ],
)
def test_out_of_range_or_unknown_values_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        UserProfile.model_validate({"language": "en", field: value})


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        UserProfile.model_validate({"language": "en", "aadhaar_number": "1234"})


def test_assignment_is_validated() -> None:
    profile = UserProfile(language="hi")
    profile.age = 30

    with pytest.raises(ValidationError):
        profile.age = 200


def test_field_sources_record_provenance() -> None:
    source = FieldSource(
        source="document", valid_until=None, captured_at=datetime.now(UTC), confidence=0.9
    )
    profile = UserProfile(
        language="en",
        annual_family_income_inr=180000,
        field_sources={"annual_family_income_inr": source},
    )

    assert profile.field_sources["annual_family_income_inr"].source == "document"
    round_trip = UserProfile.model_validate_json(profile.model_dump_json())
    assert round_trip == profile


def test_field_sources_must_name_profile_fields() -> None:
    with pytest.raises(ValidationError, match="unknown fields: income"):
        UserProfile.model_validate(
            {
                "language": "en",
                "field_sources": {
                    "income": {
                        "source": "self_declared",
                        "captured_at": "2026-10-17T10:00:00Z",
                        "confidence": 1,
                    }
                },
            }
        )


def test_field_source_confidence_is_a_probability() -> None:
    with pytest.raises(ValidationError):
        FieldSource(source="self_declared", captured_at=datetime.now(UTC), confidence=1.5)


def test_catalogue_is_derived_from_the_profile() -> None:
    assert set(PROFILE_FIELDS) == set(UserProfile.model_fields) - NON_ELIGIBILITY_FIELDS
    assert PROFILE_FIELDS["age"].kind == "int"
    assert PROFILE_FIELDS["land_holding_acres"].kind == "float"
    assert PROFILE_FIELDS["is_bpl"].kind == "bool"
    assert PROFILE_FIELDS["district"].kind == "str"
    assert PROFILE_FIELDS["residence"].choices == ("rural", "urban")
    assert PROFILE_FIELDS["age"].description == "Age in completed years"


def test_sensitive_fields_are_marked() -> None:
    sensitive = {name for name, spec in PROFILE_FIELDS.items() if spec.sensitive}

    assert sensitive == {
        "social_category",
        "caste_name",
        "religion",
        "is_minority",
        "disability_percent",
        "disability",
    }


@pytest.mark.parametrize(
    ("field", "value", "problem"),
    [
        ("age", 30, None),
        ("age", 30.5, "30.5 is not a valid int for age"),
        ("age", True, "True is not a valid int for age"),
        ("age", 140, "140 is not a valid age: input should be less than or equal to 130"),
        ("land_holding_acres", 2, None),
        ("is_bpl", 1, "1 is not a valid bool for is_bpl"),
        ("state", "MH", None),
        ("state", "mh", "'mh' is not a valid state: write it as 'MH'"),
        ("state", "Maharashtra", "'Maharashtra' is not a valid state: string should match"),
        ("gender", "f", "'f' is not one of: male, female, other"),
        ("district", "Pune", None),
    ],
)
def test_value_problems(field: str, value: object, problem: str | None) -> None:
    found = PROFILE_FIELDS[field].value_problem(value)

    if problem is None:
        assert found is None
    else:
        assert found is not None
        assert found.startswith(problem)
