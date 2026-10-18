"""Request and response bodies of the public API (TRD §11)."""

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from sahayak.agent.state import ConsentPurpose, Intent, ReplyKind
from sahayak.db.models import Channel, SchemeLevel, SchemeStatus
from sahayak.eligibility.profile import Language

MAX_MESSAGE_CHARS = 2000


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateSessionRequest(_Body):
    channel: Channel = Channel.WEB
    language: Language | None = Field(None, description="Preferred language, if known")


class ConsentRecord(BaseModel):
    granted_at: datetime
    policy_version: str


class SessionResponse(BaseModel):
    session_id: uuid.UUID
    channel: Channel
    language: Language | None
    expires_at: datetime
    consents: dict[str, ConsentRecord]


class ConsentRequest(_Body):
    purposes: list[ConsentPurpose] = Field(min_length=1)
    granted: bool = Field(True, description="false withdraws the consents")
    policy_version: str = Field(min_length=1, max_length=40, pattern=r"^[\w.\-]+$")


class ConsentResponse(BaseModel):
    consents: dict[str, ConsentRecord]


class MessageRequest(_Body):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class SchemeResult(BaseModel):
    scheme_id: str
    name: str
    provisional: bool
    reasons: list[str] = Field(description="Rule conditions the profile meets")
    official_url: str | None
    last_verified_at: datetime | None


class EligibilityGroups(BaseModel):
    """The filtered recommendations (TRD §8C). Only schemes the user is eligible for."""

    ready: list[SchemeResult]
    action_needed: list[SchemeResult]
    provisional: list[SchemeResult] = Field(description="Not yet verified: show with a caveat")


class CitationResponse(BaseModel):
    number: int
    chunk_id: str
    scheme_id: str
    scheme_name: str
    section: str
    source_url: str
    last_verified_at: datetime | None


class MessageResponse(BaseModel):
    reply_text: str
    reply_audio_url: str | None = None  # voice replies arrive in M5
    kind: ReplyKind
    language: Language
    intent: Intent | None
    question_field: str | None = Field(description="The profile field asked about, if any")
    eligibility: EligibilityGroups
    citations: list[CitationResponse]
    session_deleted: bool = False


class FieldSourceResponse(BaseModel):
    source: str
    captured_at: datetime
    confidence: float
    valid_until: date | None = None


class ProfileResponse(BaseModel):
    session_id: uuid.UUID
    language: Language | None
    profile: dict[str, Any] = Field(description="Known profile values; unknown ones are left out")
    field_sources: dict[str, FieldSourceResponse]


class SchemeSummary(BaseModel):
    id: str
    name: str
    names: dict[Language, str]
    level: SchemeLevel
    state: str | None
    status: SchemeStatus
    benefits: str | None
    official_url: str | None
    last_verified_at: datetime | None


class SchemeDetail(SchemeSummary):
    aliases: list[str]
    ministry: str | None
    documents_required: list[Any]
    application_url: str | None
    source_url: str | None


class SchemeList(BaseModel):
    schemes: list[SchemeSummary]
