"""ORM models for the catalogue, retrieval index, rules, sessions and evals (TRD §9.3).

Enum-like columns are VARCHAR with CHECK constraints rather than native Postgres enums, so adding
a value later is a one-line constraint change instead of an `ALTER TYPE` migration.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, Mapper, mapped_column, relationship

from sahayak.db.base import Base
from sahayak.eligibility.rules import validate_rule

# Dimension shared by multilingual-e5-large and text-embedding-3-large (requested at 1024).
EMBEDDING_DIM = 1024


class Language(StrEnum):
    HI = "hi"
    MR = "mr"
    EN = "en"


class SchemeLevel(StrEnum):
    CENTRAL = "central"
    STATE = "state"


class SchemeStatus(StrEnum):
    PROVISIONAL = "provisional"
    ACTIVE = "active"
    DISCONTINUED = "discontinued"
    ARCHIVED = "archived"


class RuleStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    ARCHIVED = "archived"
    REJECTED = "rejected"


class Channel(StrEnum):
    WEB = "web"
    WHATSAPP = "whatsapp"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


def _str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """VARCHAR + CHECK constraint storing the enum's values (not its member names)."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Scheme(Base):
    __tablename__ = "schemes"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)  # slug, e.g. "pm-kisan"
    name_en: Mapped[str] = mapped_column(Text)
    name_hi: Mapped[str | None] = mapped_column(Text)
    name_mr: Mapped[str | None] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=sql_text("'{}'::text[]"), default=list
    )
    ministry: Mapped[str | None] = mapped_column(Text)
    level: Mapped[SchemeLevel] = mapped_column(_str_enum(SchemeLevel, "scheme_level"))
    state: Mapped[str | None] = mapped_column(String(2))  # ISO 3166-2:IN code, e.g. "MH"
    benefits: Mapped[str | None] = mapped_column(Text)
    application_url: Mapped[str | None] = mapped_column(Text)
    documents_required: Mapped[list[Any]] = mapped_column(
        JSONB, server_default=sql_text("'[]'::jsonb"), default=list
    )
    form_template_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[SchemeStatus] = mapped_column(
        _str_enum(SchemeStatus, "scheme_status"),
        server_default=SchemeStatus.PROVISIONAL.value,
        default=SchemeStatus.PROVISIONAL,
    )
    launched_at: Mapped[date | None] = mapped_column(Date)
    ends_at: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str | None] = mapped_column(Text)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    chunks: Mapped[list["SchemeChunk"]] = relationship(
        back_populates="scheme", cascade="all, delete-orphan", passive_deletes=True
    )
    rules: Mapped[list["EligibilityRule"]] = relationship(
        back_populates="scheme", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("level = 'central' OR state IS NOT NULL", name="state_required"),
        Index("ix_schemes_status", "status"),
    )


# Postgres text-search config per chunk language: stemming for English, plain tokens for hi/mr
# (Postgres ships no Hindi/Marathi stemmer). Must stay immutable to be usable in a generated column.
TSV_EXPRESSION = (
    "to_tsvector(CASE WHEN language = 'en' THEN 'english'::regconfig ELSE 'simple'::regconfig END, "
    "coalesce(section, '') || ' ' || text)"
)


class SchemeChunk(Base):
    __tablename__ = "scheme_chunks"

    # Deterministic (uuid5 of scheme + content hash), so citations stay stable across re-ingests.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    scheme_id: Mapped[str] = mapped_column(ForeignKey("schemes.id", ondelete="CASCADE"))
    source_url: Mapped[str] = mapped_column(Text)
    section: Mapped[str] = mapped_column(Text)
    language: Mapped[Language] = mapped_column(_str_enum(Language, "chunk_language"))
    chunk_index: Mapped[int] = mapped_column(Integer)  # position within its source document
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    tsv: Mapped[str] = mapped_column(TSVECTOR, Computed(TSV_EXPRESSION, persisted=True))
    created_at: Mapped[datetime] = _created_at()

    scheme: Mapped[Scheme] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("scheme_id", "content_hash"),
        Index("ix_scheme_chunks_scheme_id_source_url", "scheme_id", "source_url"),
        Index("ix_scheme_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_scheme_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class EligibilityRule(Base):
    __tablename__ = "eligibility_rules"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scheme_id: Mapped[str] = mapped_column(ForeignKey("schemes.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[RuleStatus] = mapped_column(
        _str_enum(RuleStatus, "rule_status"),
        server_default=RuleStatus.DRAFT.value,
        default=RuleStatus.DRAFT,
    )
    logic: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source_refs: Mapped[list[Any]] = mapped_column(
        JSONB, server_default=sql_text("'[]'::jsonb"), default=list
    )
    notes: Mapped[str | None] = mapped_column(Text)
    # Used from M10 (TRD §8C); kept here so the rule row holds the whole DSL document.
    required_documents: Mapped[list[Any] | None] = mapped_column(JSONB)
    application_window: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Hash of the eligibility text the draft was extracted from: re-ingesting unchanged text
    # does not create another draft.
    source_hash: Mapped[str | None] = mapped_column(String(64))
    # Model, prompt version, confidence and criteria the DSL could not express.
    extraction: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    review_edited: Mapped[bool] = mapped_column(
        Boolean, server_default=sql_text("false"), default=False
    )
    reviewed_by: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()

    scheme: Mapped[Scheme] = relationship(back_populates="rules")

    __table_args__ = (
        UniqueConstraint("scheme_id", "version"),
        # At most one live rule per scheme.
        Index(
            "uq_eligibility_rules_one_approved",
            "scheme_id",
            unique=True,
            postgresql_where=sql_text("status = 'approved'"),
        ),
    )

    def document(self) -> dict[str, Any]:
        """The rule as a DSL document (`sahayak.eligibility.rules.SchemeRule` input)."""
        return {
            "scheme_id": self.scheme_id,
            "version": self.version,
            "status": RuleStatus(self.status).value,
            "logic": self.logic,
            "source_refs": self.source_refs or [],
            "notes": self.notes,
            "required_documents": self.required_documents,
            "application_window": self.application_window,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
        }


@event.listens_for(EligibilityRule, "before_insert")
@event.listens_for(EligibilityRule, "before_update")
def _check_live_rule(_mapper: Mapper[Any], _connection: Connection, rule: EligibilityRule) -> None:
    """No code path may save an invalid approved rule (M2-T04). Drafts may hold issues: they
    are the reviewer's input, and approval is blocked until they are fixed (D-019)."""
    if rule.status == RuleStatus.APPROVED:
        validate_rule(rule.document())


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    channel: Mapped[Channel] = mapped_column(_str_enum(Channel, "session_channel"))
    # Salted SHA-256 of the sender's phone number; never the raw number.
    channel_user_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    language: Mapped[Language | None] = mapped_column(_str_enum(Language, "session_language"))
    created_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    profile: Mapped["Profile | None"] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # M10 adds member_id for signed-in users; anonymous profiles hang off the session.
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), unique=True
    )
    profile: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=sql_text("'{}'::jsonb"), default=dict
    )
    field_sources: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=sql_text("'{}'::jsonb"), default=dict
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    session: Mapped[Session] = relationship(back_populates="profile")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    role: Mapped[MessageRole] = mapped_column(_str_enum(MessageRole, "message_role"))
    text: Mapped[str] = mapped_column(Text)
    audio_ref: Mapped[str | None] = mapped_column(Text)
    lang: Mapped[Language | None] = mapped_column(_str_enum(Language, "message_language"))
    asr_confidence: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created_at()

    session: Mapped[Session] = relationship(back_populates="messages")

    __table_args__ = (Index("ix_messages_session_id_created_at", "session_id", "created_at"),)


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    suite: Mapped[str] = mapped_column(Text)
    git_sha: Mapped[str | None] = mapped_column(String(40))
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=sql_text("'{}'::jsonb"), default=dict
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=sql_text("'{}'::jsonb"), default=dict
    )
    created_at: Mapped[datetime] = _created_at()
