import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.db.models import (
    EMBEDDING_DIM,
    Channel,
    EligibilityRule,
    Language,
    Message,
    MessageRole,
    Profile,
    RuleStatus,
    Scheme,
    SchemeChunk,
    SchemeLevel,
    SchemeStatus,
    Session,
)
from tests.integration.conftest import alembic_config


def make_scheme(scheme_id: str = "pm-kisan", **overrides: object) -> Scheme:
    values: dict[str, object] = {"id": scheme_id, "name_en": "PM-KISAN", "level": "central"}
    values.update(overrides)
    return Scheme(**values)


def make_chunk(
    scheme_id: str, text_: str, language: Language, embedding: list[float]
) -> SchemeChunk:
    return SchemeChunk(
        id=uuid.uuid4(),
        scheme_id=scheme_id,
        source_url="https://example.gov.in/scheme",
        section="Eligibility",
        language=language,
        chunk_index=0,
        text=text_,
        token_count=10,
        content_hash=uuid.uuid4().hex * 2,
        embedding=embedding,
    )


def unit_vector(index: int) -> list[float]:
    vector = [0.0] * EMBEDDING_DIM
    vector[index] = 1.0
    return vector


def test_models_match_migrations(database_url: str) -> None:
    """Autogenerate finds nothing to do: the hand-written migrations match the ORM models."""
    command.check(alembic_config(database_url))


async def test_scheme_defaults(db_session: AsyncSession) -> None:
    db_session.add(make_scheme())
    await db_session.flush()

    scheme = await db_session.get(Scheme, "pm-kisan")

    assert scheme is not None
    assert scheme.status is SchemeStatus.PROVISIONAL
    assert scheme.level is SchemeLevel.CENTRAL
    assert scheme.aliases == []
    assert scheme.documents_required == []


async def test_state_scheme_requires_state(db_session: AsyncSession) -> None:
    db_session.add(make_scheme("mmlby", level="state"))

    with pytest.raises(IntegrityError, match="ck_schemes_state_required"):
        await db_session.flush()


async def test_status_check_constraint_rejects_unknown_values(db_session: AsyncSession) -> None:
    await db_session.execute(
        text("INSERT INTO schemes (id, name_en, level) VALUES ('x', 'X', 'central')")
    )

    with pytest.raises(IntegrityError, match="ck_schemes_scheme_status"):
        await db_session.execute(text("UPDATE schemes SET status = 'live' WHERE id = 'x'"))


async def test_tsv_is_generated_per_language(db_session: AsyncSession) -> None:
    db_session.add(make_scheme())
    english = make_chunk("pm-kisan", "Farmers holding cultivable land", Language.EN, unit_vector(0))
    hindi = make_chunk("pm-kisan", "किसानों के लिए योजना", Language.HI, unit_vector(1))
    db_session.add_all([english, hindi])
    await db_session.flush()

    stemmed = await db_session.scalar(
        select(SchemeChunk.id).where(
            SchemeChunk.tsv.op("@@")(text("to_tsquery('english', 'farmer')"))
        )
    )
    hindi_hit = await db_session.scalar(
        select(SchemeChunk.id).where(
            SchemeChunk.tsv.op("@@")(text("to_tsquery('simple', 'किसानों')"))
        )
    )
    section_hit = await db_session.scalar(
        select(SchemeChunk.id).where(
            SchemeChunk.tsv.op("@@")(text("to_tsquery('simple', 'eligibility')")),
            SchemeChunk.language == Language.HI,
        )
    )

    assert stemmed == english.id  # "farmers" matched "farmer" through the English stemmer
    assert hindi_hit == hindi.id
    assert section_hit == hindi.id  # the section heading is searchable too


async def test_cosine_search_orders_by_similarity(db_session: AsyncSession) -> None:
    db_session.add(make_scheme())
    near = make_chunk("pm-kisan", "near", Language.EN, unit_vector(0))
    far = make_chunk("pm-kisan", "far", Language.EN, unit_vector(5))
    db_session.add_all([far, near])
    await db_session.flush()

    query = [0.0] * EMBEDDING_DIM
    query[0], query[5] = 0.9, 0.1
    ids = (
        await db_session.scalars(
            select(SchemeChunk.id).order_by(SchemeChunk.embedding.cosine_distance(query))
        )
    ).all()

    assert ids == [near.id, far.id]


async def test_hnsw_and_gin_indexes_exist(db_session: AsyncSession) -> None:
    rows = await db_session.execute(
        text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'scheme_chunks'")
    )
    indexes = {str(name): str(definition) for name, definition in rows}

    assert "USING hnsw (embedding vector_cosine_ops)" in indexes["ix_scheme_chunks_embedding"]
    assert "USING gin (tsv)" in indexes["ix_scheme_chunks_tsv"]


async def test_chunk_content_hash_is_unique_per_scheme(db_session: AsyncSession) -> None:
    db_session.add(make_scheme())
    first = make_chunk("pm-kisan", "a", Language.EN, unit_vector(0))
    second = make_chunk("pm-kisan", "b", Language.EN, unit_vector(1))
    second.content_hash = first.content_hash
    db_session.add_all([first, second])

    with pytest.raises(IntegrityError, match="uq_scheme_chunks_scheme_id"):
        await db_session.flush()


async def test_only_one_approved_rule_per_scheme(db_session: AsyncSession) -> None:
    db_session.add(make_scheme())
    logic = {"all": [{"field": "occupation", "op": "in", "value": ["farmer"]}]}
    db_session.add_all(
        [
            EligibilityRule(scheme_id="pm-kisan", version=1, logic=logic),
            EligibilityRule(scheme_id="pm-kisan", version=2, logic=logic),
        ]
    )
    await db_session.flush()
    await db_session.execute(
        text("UPDATE eligibility_rules SET status = 'approved' WHERE version = 1")
    )

    with pytest.raises(IntegrityError, match="uq_eligibility_rules_one_approved"):
        await db_session.execute(
            text("UPDATE eligibility_rules SET status = 'approved' WHERE version = 2")
        )


async def test_new_rule_is_a_draft(db_session: AsyncSession) -> None:
    db_session.add(make_scheme())
    rule = EligibilityRule(scheme_id="pm-kisan", version=1, logic={"all": []})
    db_session.add(rule)
    await db_session.flush()

    assert rule.status is RuleStatus.DRAFT
    assert rule.review_edited is False


async def test_deleting_a_session_deletes_profile_and_messages(db_session: AsyncSession) -> None:
    session = Session(channel=Channel.WEB, expires_at=datetime.now(UTC) + timedelta(days=30))
    db_session.add(session)
    await db_session.flush()
    db_session.add_all(
        [
            Profile(session_id=session.id, profile={"state": "MH"}),
            Message(session_id=session.id, role=MessageRole.USER, text="namaste"),
        ]
    )
    await db_session.flush()

    await db_session.execute(text("DELETE FROM sessions WHERE id = :id"), {"id": session.id})

    assert await db_session.scalar(select(Profile.id)) is None
    assert await db_session.scalar(select(Message.id)) is None
