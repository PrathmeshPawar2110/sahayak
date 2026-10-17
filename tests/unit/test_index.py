from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from sahayak.db.models import Language
from sahayak.ingestion.chunk import chunk_sections
from sahayak.ingestion.index import (
    IndexStats,
    SourceChunks,
    embedding_input,
    index_scheme,
    index_source_chunks,
    sources_hash,
)
from sahayak.ingestion.manifest import SchemeSpec
from sahayak.ingestion.parse import Section
from sahayak.rag.embeddings.fake import FakeEmbeddingProvider

URL = "https://example.gov.in/scheme"
SPEC = SchemeSpec.model_validate(
    {"id": "pm-kisan", "names": {"en": "PM-KISAN"}, "level": "central", "sources": [{"url": URL}]}
)


def chunks_for(scheme_id: str, url: str) -> SourceChunks:
    chunks = chunk_sections(
        [Section("Eligibility", "Farmers.")],
        scheme_id=scheme_id,
        source_url=url,
        language=Language.EN,
    )
    return SourceChunks(url=url, sha256="abc", chunks=chunks)


def test_embedding_input_prefixes_scheme_and_section() -> None:
    (chunk,) = chunks_for("pm-kisan", URL).chunks

    assert embedding_input("PM-KISAN", chunk) == "PM-KISAN: Eligibility\nFarmers."


def test_sources_hash_ignores_order_and_detects_change() -> None:
    assert sources_hash(["a", "b"]) == sources_hash(["b", "a"])
    assert sources_hash(["a", "b"]) != sources_hash(["a", "c"])
    assert len(sources_hash([])) == 64


def test_index_stats_add() -> None:
    assert IndexStats(1, 2, 3) + IndexStats(added=10) == IndexStats(11, 2, 3)


async def test_index_scheme_rejects_sources_outside_the_manifest() -> None:
    session = AsyncMock()

    with pytest.raises(ValueError, match="not in the manifest"):
        await index_scheme(
            session,
            FakeEmbeddingProvider(),
            SPEC,
            [chunks_for("pm-kisan", "https://elsewhere.gov.in/")],
            verified_at=datetime.now(UTC),
        )
    session.execute.assert_not_called()


async def test_index_scheme_rejects_duplicate_sources() -> None:
    session = AsyncMock()
    source = chunks_for("pm-kisan", URL)

    with pytest.raises(ValueError, match="listed twice"):
        await index_scheme(
            session, FakeEmbeddingProvider(), SPEC, [source, source], verified_at=datetime.now(UTC)
        )
    session.execute.assert_not_called()


async def test_index_source_chunks_rejects_chunks_of_another_scheme() -> None:
    session = AsyncMock()

    with pytest.raises(ValueError, match="do not belong"):
        await index_source_chunks(
            session,
            FakeEmbeddingProvider(),
            scheme_id="pm-kisan",
            scheme_name="PM-KISAN",
            source_url=URL,
            chunks=chunks_for("pmay-g", URL).chunks,
        )
    session.execute.assert_not_called()
