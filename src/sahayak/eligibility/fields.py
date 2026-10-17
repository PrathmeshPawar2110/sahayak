"""Profile fields that eligibility rules may test (TRD §9.1).

Until M2-T02 builds the `UserProfile` model, this catalogue is the contract between rule
extraction and the rule engine: a rule may only name a field listed here, with a value of that
field's type. `ProfileFieldName` lists the same names statically so they can be offered to the LLM
as an enum.
"""

from dataclasses import dataclass
from typing import Literal

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


@dataclass(frozen=True)
class ProfileField:
    name: str
    kind: FieldKind
    description: str
    choices: tuple[str, ...] = ()
    sensitive: bool = False  # TRD §8B: optional, consent-gated, never inferred


PROFILE_FIELDS: dict[str, ProfileField] = {
    field.name: field
    for field in (
        ProfileField("state", "str", "State of residence as an ISO 3166-2:IN code, e.g. MH"),
        ProfileField("district", "str", "District of residence"),
        ProfileField("age", "int", "Age in completed years"),
        ProfileField("gender", "enum", "Gender", ("male", "female", "other")),
        ProfileField(
            "annual_family_income_inr", "int", "Total annual income of the family, in rupees"
        ),
        ProfileField(
            "social_category",
            "enum",
            "Social category",
            ("general", "obc", "sc", "st", "ews", "vjnt", "sbc", "nt"),
            sensitive=True,
        ),
        ProfileField("caste_name", "str", "Name of the caste", sensitive=True),
        ProfileField(
            "caste_validity_verified", "bool", "Holds a Maharashtra caste validity certificate"
        ),
        ProfileField("is_non_creamy_layer", "bool", "Belongs to the non-creamy layer"),
        ProfileField("religion", "str", "Religion, self-declared", sensitive=True),
        ProfileField("is_minority", "bool", "Belongs to a notified minority", sensitive=True),
        ProfileField("domicile_state", "str", "State of domicile as an ISO 3166-2:IN code"),
        ProfileField(
            "disability_percent", "int", "Certified disability percentage", sensitive=True
        ),
        ProfileField("current_course", "str", "Course currently being studied"),
        ProfileField("marks_percent", "float", "Percentage in the last qualifying exam"),
        ProfileField(
            "occupation",
            "enum",
            "Main occupation",
            (
                "farmer",
                "agri_labourer",
                "daily_wage",
                "street_vendor",
                "artisan",
                "student",
                "salaried",
                "self_employed",
                "unemployed",
                "homemaker",
                "other",
            ),
        ),
        ProfileField("land_holding_acres", "float", "Cultivable land owned, in acres"),
        ProfileField("is_bpl", "bool", "Family is below the poverty line"),
        ProfileField(
            "has_ration_card", "enum", "Ration card held", ("none", "apl", "bpl", "aay", "phh")
        ),
        ProfileField("disability", "bool", "Has a disability", sensitive=True),
        ProfileField("is_income_tax_payer", "bool", "Anyone in the family paid income tax"),
        ProfileField("is_govt_employee", "bool", "Is a serving or retired government employee"),
        ProfileField(
            "marital_status", "enum", "Marital status", ("single", "married", "widowed", "divorced")
        ),
        ProfileField("num_children", "int", "Number of children"),
        ProfileField("education_level", "str", "Highest education completed"),
        ProfileField("residence", "enum", "Rural or urban residence", ("rural", "urban")),
    )
}
