"""catalogue, chunks, rules, sessions, profiles, messages, eval runs

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LANGUAGES = "('hi', 'mr', 'en')"
TSV_EXPRESSION = (
    "to_tsvector(CASE WHEN language = 'en' THEN 'english'::regconfig ELSE 'simple'::regconfig END, "
    "coalesce(section, '') || ' ' || text)"
)


def upgrade() -> None:
    op.create_table(
        "schemes",
        sa.Column("id", sa.String(100), nullable=False),
        sa.Column("name_en", sa.Text(), nullable=False),
        sa.Column("name_hi", sa.Text(), nullable=True),
        sa.Column("name_mr", sa.Text(), nullable=True),
        sa.Column(
            "aliases",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("ministry", sa.Text(), nullable=True),
        sa.Column("level", sa.String(20), nullable=False),
        sa.Column("state", sa.String(2), nullable=True),
        sa.Column("benefits", sa.Text(), nullable=True),
        sa.Column("application_url", sa.Text(), nullable=True),
        sa.Column(
            "documents_required",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("form_template_id", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), server_default="provisional", nullable=False),
        sa.Column("launched_at", sa.Date(), nullable=True),
        sa.Column("ends_at", sa.Date(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("level IN ('central', 'state')", name=op.f("ck_schemes_scheme_level")),
        sa.CheckConstraint(
            "status IN ('provisional', 'active', 'discontinued', 'archived')",
            name=op.f("ck_schemes_scheme_status"),
        ),
        sa.CheckConstraint(
            "level = 'central' OR state IS NOT NULL", name=op.f("ck_schemes_state_required")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schemes")),
    )
    op.create_index("ix_schemes_status", "schemes", ["status"])

    op.create_table(
        "scheme_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scheme_id", sa.String(100), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(1024), nullable=False),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed(TSV_EXPRESSION, persisted=True),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            f"language IN {LANGUAGES}", name=op.f("ck_scheme_chunks_chunk_language")
        ),
        sa.ForeignKeyConstraint(
            ["scheme_id"],
            ["schemes.id"],
            name=op.f("fk_scheme_chunks_scheme_id_schemes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scheme_chunks")),
        sa.UniqueConstraint("scheme_id", "content_hash", name=op.f("uq_scheme_chunks_scheme_id")),
    )
    op.create_index(
        "ix_scheme_chunks_scheme_id_source_url", "scheme_chunks", ["scheme_id", "source_url"]
    )
    op.create_index("ix_scheme_chunks_tsv", "scheme_chunks", ["tsv"], postgresql_using="gin")
    op.create_index(
        "ix_scheme_chunks_embedding",
        "scheme_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "eligibility_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scheme_id", sa.String(100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("logic", postgresql.JSONB(), nullable=False),
        sa.Column(
            "source_refs", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("required_documents", postgresql.JSONB(), nullable=True),
        sa.Column("application_window", postgresql.JSONB(), nullable=True),
        sa.Column("source_hash", sa.String(64), nullable=True),
        sa.Column("extraction", postgresql.JSONB(), nullable=True),
        sa.Column("review_edited", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("reviewed_by", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'archived', 'rejected')",
            name=op.f("ck_eligibility_rules_rule_status"),
        ),
        sa.ForeignKeyConstraint(
            ["scheme_id"],
            ["schemes.id"],
            name=op.f("fk_eligibility_rules_scheme_id_schemes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eligibility_rules")),
        sa.UniqueConstraint("scheme_id", "version", name=op.f("uq_eligibility_rules_scheme_id")),
    )
    op.create_index(
        "uq_eligibility_rules_one_approved",
        "eligibility_rules",
        ["scheme_id"],
        unique=True,
        postgresql_where=sa.text("status = 'approved'"),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("channel_user_hash", sa.String(64), nullable=True),
        sa.Column("language", sa.String(20), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "channel IN ('web', 'whatsapp')", name=op.f("ck_sessions_session_channel")
        ),
        sa.CheckConstraint(f"language IN {LANGUAGES}", name=op.f("ck_sessions_session_language")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
    )
    op.create_index(op.f("ix_sessions_channel_user_hash"), "sessions", ["channel_user_hash"])
    op.create_index(op.f("ix_sessions_expires_at"), "sessions", ["expires_at"])

    op.create_table(
        "profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column(
            "profile", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "field_sources",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_profiles_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profiles")),
        sa.UniqueConstraint("session_id", name=op.f("uq_profiles_session_id")),
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("audio_ref", sa.Text(), nullable=True),
        sa.Column("lang", sa.String(20), nullable=True),
        sa.Column("asr_confidence", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system')", name=op.f("ck_messages_message_role")
        ),
        sa.CheckConstraint(f"lang IN {LANGUAGES}", name=op.f("ck_messages_message_language")),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_messages_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
    )
    op.create_index("ix_messages_session_id_created_at", "messages", ["session_id", "created_at"])

    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("suite", sa.Text(), nullable=False),
        sa.Column("git_sha", sa.String(40), nullable=True),
        sa.Column(
            "config", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "metrics", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_runs")),
    )


def downgrade() -> None:
    op.drop_table("eval_runs")
    op.drop_table("messages")
    op.drop_table("profiles")
    op.drop_table("sessions")
    op.drop_table("eligibility_rules")
    op.drop_table("scheme_chunks")
    op.drop_table("schemes")
