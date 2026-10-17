from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.db.models import Language, Scheme, SchemeChunk, SchemeStatus
from sahayak.ingestion import index
from sahayak.ingestion.chunk import Chunk, chunk_sections
from sahayak.ingestion.index import IndexStats, SourceChunks, index_scheme, sources_hash
from sahayak.ingestion.manifest import SchemeSpec
from sahayak.ingestion.parse import Section
from sahayak.rag.embeddings.fake import FakeEmbeddingProvider

GUIDELINES = "https://example.gov.in/pm-kisan/guidelines"
FAQ = "https://example.gov.in/pm-kisan/faq"
VERIFIED_AT = datetime(2026, 10, 1, tzinfo=UTC)
LATER = datetime(2026, 10, 15, tzinfo=UTC)

SECTIONS = [
    Section("Eligibility", "All landholding farmer families with cultivable land are eligible."),
    Section("Exclusions", "Institutional landholders and income tax payers are excluded."),
    Section("Benefits", "Rs 6000 per year in three equal instalments by direct benefit transfer."),
]
FAQ_SECTIONS = [Section("प्रश्न", "किसान परिवार को प्रति वर्ष 6000 रुपये मिलते हैं।")]


def make_spec(**overrides: object) -> SchemeSpec:
    data: dict[str, object] = {
        "id": "pm-kisan",
        "names": {"en": "PM-KISAN", "hi": "पीएम-किसान"},
        "level": "central",
        "sources": [{"url": GUIDELINES}, {"url": FAQ, "language": "hi"}],
    }
    data.update(overrides)
    return SchemeSpec.model_validate(data)


def source(url: str, sections: list[Section], language: Language = Language.EN) -> SourceChunks:
    chunks = chunk_sections(sections, scheme_id="pm-kisan", source_url=url, language=language)
    return SourceChunks(url=url, sha256=f"sha-{len(chunks)}-{url}", chunks=chunks)


def both_sources(sections: list[Section] = SECTIONS) -> list[SourceChunks]:
    return [source(GUIDELINES, sections), source(FAQ, FAQ_SECTIONS, Language.HI)]


async def stored_chunks(session: AsyncSession) -> list[SchemeChunk]:
    result = await session.scalars(
        select(SchemeChunk).order_by(SchemeChunk.source_url, SchemeChunk.chunk_index)
    )
    return list(result.all())


async def chunk_count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(SchemeChunk)) or 0


async def test_first_index_creates_scheme_and_searchable_chunks(db_session: AsyncSession) -> None:
    embedder = FakeEmbeddingProvider()
    sources = both_sources()

    stats = await index_scheme(db_session, embedder, make_spec(), sources, verified_at=VERIFIED_AT)

    assert stats == IndexStats(added=4, kept=0, removed=0)
    scheme = await db_session.get(Scheme, "pm-kisan")
    assert scheme is not None
    assert scheme.status is SchemeStatus.PROVISIONAL
    assert scheme.name_hi == "पीएम-किसान"
    assert scheme.source_url == GUIDELINES
    assert scheme.last_verified_at == VERIFIED_AT
    assert scheme.content_hash == sources_hash([s.sha256 for s in sources])

    chunks = await stored_chunks(db_session)
    expected: list[Chunk] = [*sources[1].chunks, *sources[0].chunks]  # FAQ URL sorts first
    assert [c.id for c in chunks] == [c.id for c in expected]
    assert {c.language for c in chunks} == {Language.EN, Language.HI}

    # Embedded as passages with the scheme name in the chunk's language.
    assert [kind for kind, _ in embedder.calls] == ["passage", "passage"]
    assert embedder.calls[1][1] == ["पीएम-किसान: प्रश्न\nकिसान परिवार को प्रति वर्ष 6000 रुपये मिलते हैं।"]

    hit = await db_session.scalar(
        select(SchemeChunk.section).where(
            SchemeChunk.tsv.op("@@")(text("to_tsquery('english', 'instalment')"))
        )
    )
    assert hit == "Benefits"
    nearest = await db_session.scalar(
        select(SchemeChunk.section).order_by(
            SchemeChunk.embedding.cosine_distance(
                embedder.vector("PM-KISAN: Exclusions income tax payers excluded")
            )
        )
    )
    assert nearest == "Exclusions"


async def test_rerun_is_idempotent_and_embeds_nothing(db_session: AsyncSession) -> None:
    await index_scheme(
        db_session, FakeEmbeddingProvider(), make_spec(), both_sources(), verified_at=VERIFIED_AT
    )
    before = [(c.id, c.chunk_index, c.text) for c in await stored_chunks(db_session)]
    embedder = FakeEmbeddingProvider()

    stats = await index_scheme(db_session, embedder, make_spec(), both_sources(), verified_at=LATER)

    assert stats == IndexStats(added=0, kept=4, removed=0)
    assert embedder.calls == []
    assert [(c.id, c.chunk_index, c.text) for c in await stored_chunks(db_session)] == before
    scheme = await db_session.get(Scheme, "pm-kisan", populate_existing=True)
    assert scheme is not None
    assert scheme.last_verified_at == LATER


async def test_changed_section_replaces_only_its_chunk(db_session: AsyncSession) -> None:
    await index_scheme(
        db_session, FakeEmbeddingProvider(), make_spec(), both_sources(), verified_at=VERIFIED_AT
    )
    old_ids = {c.section: c.id for c in await stored_chunks(db_session)}
    changed = [
        SECTIONS[0],
        Section("Exclusions", "Serving government employees are excluded."),
        SECTIONS[2],
    ]
    embedder = FakeEmbeddingProvider()

    stats = await index_scheme(
        db_session, embedder, make_spec(), both_sources(changed), verified_at=LATER
    )

    assert stats == IndexStats(added=1, kept=3, removed=1)
    assert len(embedder.calls[0][1]) == 1
    new_ids = {c.section: c.id for c in await stored_chunks(db_session)}
    assert new_ids["Exclusions"] != old_ids["Exclusions"]
    assert new_ids["Eligibility"] == old_ids["Eligibility"]  # citations stay stable
    assert await chunk_count(db_session) == 4


async def test_moved_chunk_keeps_its_id_and_gets_a_new_index(db_session: AsyncSession) -> None:
    await index_scheme(
        db_session, FakeEmbeddingProvider(), make_spec(), both_sources(), verified_at=VERIFIED_AT
    )
    reordered = [SECTIONS[2], SECTIONS[0], SECTIONS[1]]

    stats = await index_scheme(
        db_session, FakeEmbeddingProvider(), make_spec(), both_sources(reordered), verified_at=LATER
    )

    assert stats == IndexStats(added=0, kept=4, removed=0)
    guidelines = [c for c in await stored_chunks(db_session) if c.source_url == GUIDELINES]
    assert [c.section for c in guidelines] == ["Benefits", "Eligibility", "Exclusions"]


async def test_reindex_keeps_approved_status_and_updates_names(db_session: AsyncSession) -> None:
    await index_scheme(
        db_session, FakeEmbeddingProvider(), make_spec(), both_sources(), verified_at=VERIFIED_AT
    )
    await db_session.execute(
        update(Scheme).where(Scheme.id == "pm-kisan").values(status=SchemeStatus.ACTIVE)
    )
    spec = make_spec(names={"en": "PM Kisan Samman Nidhi"}, aliases=["PM-KISAN"])

    await index_scheme(db_session, FakeEmbeddingProvider(), spec, both_sources(), verified_at=LATER)

    scheme = await db_session.get(Scheme, "pm-kisan", populate_existing=True)
    assert scheme is not None
    assert scheme.status is SchemeStatus.ACTIVE
    assert scheme.name_en == "PM Kisan Samman Nidhi"
    assert scheme.name_hi is None
    assert scheme.aliases == ["PM-KISAN"]


async def test_source_removed_from_manifest_loses_its_chunks(db_session: AsyncSession) -> None:
    await index_scheme(
        db_session, FakeEmbeddingProvider(), make_spec(), both_sources(), verified_at=VERIFIED_AT
    )
    spec = make_spec(sources=[{"url": GUIDELINES}])

    stats = await index_scheme(
        db_session, FakeEmbeddingProvider(), spec, [source(GUIDELINES, SECTIONS)], verified_at=LATER
    )

    assert stats == IndexStats(added=0, kept=3, removed=1)
    assert {c.source_url for c in await stored_chunks(db_session)} == {GUIDELINES}


async def test_unfetched_source_keeps_chunks_and_verification(db_session: AsyncSession) -> None:
    sources = both_sources()
    await index_scheme(
        db_session, FakeEmbeddingProvider(), make_spec(), sources, verified_at=VERIFIED_AT
    )

    stats = await index_scheme(
        db_session, FakeEmbeddingProvider(), make_spec(), [sources[0]], verified_at=LATER
    )

    assert stats == IndexStats(added=0, kept=3, removed=0)
    assert await chunk_count(db_session) == 4
    scheme = await db_session.get(Scheme, "pm-kisan", populate_existing=True)
    assert scheme is not None
    assert scheme.last_verified_at == VERIFIED_AT
    assert scheme.content_hash == sources_hash([s.sha256 for s in sources])


async def test_source_with_no_chunks_left_is_emptied(db_session: AsyncSession) -> None:
    await index_scheme(
        db_session, FakeEmbeddingProvider(), make_spec(), both_sources(), verified_at=VERIFIED_AT
    )
    emptied = [source(GUIDELINES, SECTIONS), SourceChunks(url=FAQ, sha256="empty", chunks=[])]

    stats = await index_scheme(
        db_session, FakeEmbeddingProvider(), make_spec(), emptied, verified_at=LATER
    )

    assert stats == IndexStats(added=0, kept=3, removed=1)
    assert await chunk_count(db_session) == 3


async def test_large_sources_are_inserted_in_batches(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(index, "INSERT_BATCH", 2)
    many = [Section(f"Part {n}", f"Clause number {n} of the guidelines.") for n in range(5)]

    stats = await index_scheme(
        db_session,
        FakeEmbeddingProvider(),
        make_spec(sources=[{"url": GUIDELINES}]),
        [source(GUIDELINES, many)],
        verified_at=VERIFIED_AT,
    )

    assert stats == IndexStats(added=5)
    assert [c.chunk_index for c in await stored_chunks(db_session)] == [0, 1, 2, 3, 4]
