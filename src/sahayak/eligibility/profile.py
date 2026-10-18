"""What Sahayak knows about a citizen (TRD §9.1), and where each value came from (TRD §8C).

Every field except `language` is optional: `None` means "not known yet", never "no". The rule
engine treats a `None` that a verdict depends on as missing information (TRD §9.2), and the
question planner asks for it. Sensitive fields (TRD §8B) are marked `sensitive` in their JSON
Schema extra: they are optional, consent-gated, and never inferred.
"""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
)

Language = Literal["hi", "mr", "en"]


def _normalise_code(value: object) -> object:
    return value.strip().upper() if isinstance(value, str) else value


StateCode = Annotated[
    str, BeforeValidator(_normalise_code), StringConstraints(pattern=r"^[A-Z]{2}$")
]
SocialCategory = Literal["general", "obc", "sc", "st", "ews", "vjnt", "sbc", "nt"]
Occupation = Literal[
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
]
RationCard = Literal["none", "apl", "bpl", "aay", "phh"]

_SENSITIVE: dict[str, JsonValue] = {"sensitive": True}


class FieldSource(BaseModel):
    """Provenance of one profile value (FR-25). Eligibility uses the strongest valid source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["self_declared", "document", "document_verified"]
    document_id: UUID | None = None
    valid_until: date | None = None  # copied from the backing document
    captured_at: datetime
    confidence: float = Field(ge=0, le=1)


class UserProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    language: Language
    state: StateCode | None = Field(
        None, description="State of residence as an ISO 3166-2:IN code, e.g. MH"
    )
    district: str | None = Field(None, description="District of residence")
    age: int | None = Field(None, ge=0, le=130, description="Age in completed years")
    gender: Literal["male", "female", "other"] | None = Field(None, description="Gender")
    annual_family_income_inr: int | None = Field(
        None, ge=0, description="Total annual income of the family, in rupees"
    )
    social_category: SocialCategory | None = Field(
        None, description="Social category", json_schema_extra=_SENSITIVE
    )
    caste_name: str | None = Field(
        None, description="Name of the caste", json_schema_extra=_SENSITIVE
    )
    caste_validity_verified: bool | None = Field(
        None, description="Holds a Maharashtra caste validity certificate"
    )
    is_non_creamy_layer: bool | None = Field(None, description="Belongs to the non-creamy layer")
    religion: str | None = Field(
        None,
        description="Religion, self-declared ('prefer_not_to_say' allowed)",
        json_schema_extra=_SENSITIVE,
    )
    is_minority: bool | None = Field(
        None, description="Belongs to a notified minority", json_schema_extra=_SENSITIVE
    )
    domicile_state: StateCode | None = Field(
        None, description="State of domicile as an ISO 3166-2:IN code"
    )
    disability_percent: int | None = Field(
        None,
        ge=0,
        le=100,
        description="Certified disability percentage",
        json_schema_extra=_SENSITIVE,
    )
    current_course: str | None = Field(None, description="Course currently being studied")
    marks_percent: float | None = Field(
        None, ge=0, le=100, description="Percentage in the last qualifying exam"
    )
    occupation: Occupation | None = Field(None, description="Main occupation")
    land_holding_acres: float | None = Field(
        None, ge=0, description="Cultivable land owned, in acres"
    )
    is_bpl: bool | None = Field(None, description="Family is below the poverty line")
    has_ration_card: RationCard | None = Field(None, description="Ration card held")
    disability: bool | None = Field(
        None, description="Has a disability", json_schema_extra=_SENSITIVE
    )
    is_income_tax_payer: bool | None = Field(
        None, description="Anyone in the family paid income tax"
    )
    is_govt_employee: bool | None = Field(
        None, description="Is a serving or retired government employee"
    )
    marital_status: Literal["single", "married", "widowed", "divorced"] | None = Field(
        None, description="Marital status"
    )
    num_children: int | None = Field(None, ge=0, description="Number of children")
    education_level: str | None = Field(None, description="Highest education completed")
    residence: Literal["rural", "urban"] | None = Field(
        None, description="Rural or urban residence"
    )
    # Provenance: which turn or document each field came from, and the confidence.
    field_sources: dict[str, FieldSource] = Field(default_factory=dict)

    @field_validator("field_sources")
    @classmethod
    def _sources_name_fields(cls, sources: dict[str, FieldSource]) -> dict[str, FieldSource]:
        unknown = sorted(set(sources) - set(cls.model_fields))
        if unknown:
            raise ValueError(f"field_sources for unknown fields: {', '.join(unknown)}")
        return sources
