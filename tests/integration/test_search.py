import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.db.models import SchemeStatus
from sahayak.rag.embeddings.fake import FakeEmbeddingProvider
from sahayak.rag.search import SearchFilter, vector_search
from tests.integration.rag_corpus import VERIFIED_AT, chunk_id, load_corpus

pytestmark = pytest.mark.usefixtures("corpus")


@pytest.fixture
def embedder() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider()


@pytest.fixture
async def corpus(db_session: AsyncSession, embedder: FakeEmbeddingProvider) -> None:
    await load_corpus(db_session, embedder)


async def query_vector(embedder: FakeEmbeddingProvider, text: str) -> list[float]:
    [vector] = await embedder.embed_queries([text])
    return vector


async def test_vector_search_ranks_nearest_chunks_first(
    db_session: AsyncSession, embedder: FakeEmbeddingProvider
) -> None:
    vector = await query_vector(
        embedder, "Benefits\nFarmers receive 6000 rupees income support yearly."
    )

    hits = await vector_search(db_session, vector, limit=3)

    assert len(hits) == 3
    top = hits[0]
    assert top.chunk_id == chunk_id(
        "farmer-support", "Farmers receive 6000 rupees income support yearly."
    )
    assert top.scheme_id == "farmer-support"
    assert top.section == "Benefits"
    assert top.source_url == "https://example.gov.in/farmer-support"
    assert top.scheme_last_verified_at == VERIFIED_AT
    assert top.score == pytest.approx(1.0)
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


async def test_vector_search_skips_discontinued_and_archived_schemes(
    db_session: AsyncSession, embedder: FakeEmbeddingProvider
) -> None:
    vector = await query_vector(embedder, "farmers income support loan waiver press notice")

    hits = await vector_search(db_session, vector, limit=50)

    schemes = {h.scheme_id for h in hits}
    assert schemes == {"farmer-support", "worker-pension", "mh-women", "ka-housing"}


async def test_vector_search_can_include_discontinued_schemes(
    db_session: AsyncSession, embedder: FakeEmbeddingProvider
) -> None:
    vector = await query_vector(embedder, "loan waiver")
    search_filter = SearchFilter.for_user(statuses=[SchemeStatus.ACTIVE, SchemeStatus.DISCONTINUED])

    hits = await vector_search(db_session, vector, search_filter=search_filter, limit=1)

    assert hits[0].scheme_id == "old-loan"


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("MH", {"farmer-support", "worker-pension", "mh-women"}),
        (" mh ", {"farmer-support", "worker-pension", "mh-women"}),
        ("KA", {"farmer-support", "worker-pension", "ka-housing"}),
        ("DL", {"farmer-support", "worker-pension"}),
        (None, {"farmer-support", "worker-pension", "mh-women", "ka-housing"}),
    ],
)
async def test_vector_search_keeps_central_and_own_state_schemes(
    db_session: AsyncSession,
    embedder: FakeEmbeddingProvider,
    state: str | None,
    expected: set[str],
) -> None:
    vector = await query_vector(embedder, "support for women and housing for families")

    hits = await vector_search(
        db_session, vector, search_filter=SearchFilter.for_user(state), limit=50
    )

    assert {h.scheme_id for h in hits} == expected


async def test_vector_search_restricted_to_schemes_is_exact(
    db_session: AsyncSession, embedder: FakeEmbeddingProvider
) -> None:
    # The query is closest to farmer-support, but only worker-pension's chunks may come back,
    # ranked among themselves.
    vector = await query_vector(embedder, "farmers 6000 rupees income support workers pension")

    hits = await vector_search(
        db_session,
        vector,
        search_filter=SearchFilter.for_user(scheme_ids=["worker-pension"]),
        limit=5,
    )

    assert [h.section for h in hits] == ["Benefits", "Contribution"]
    assert {h.scheme_id for h in hits} == {"worker-pension"}
    assert hits[0].score > hits[1].score


async def test_vector_search_scheme_restriction_still_applies_status_and_state(
    db_session: AsyncSession, embedder: FakeEmbeddingProvider
) -> None:
    vector = await query_vector(embedder, "rupees")

    hits = await vector_search(
        db_session,
        vector,
        search_filter=SearchFilter.for_user("KA", scheme_ids=["mh-women", "old-loan"]),
        limit=5,
    )

    assert hits == []


async def test_vector_search_with_no_limit_returns_nothing(
    db_session: AsyncSession, embedder: FakeEmbeddingProvider
) -> None:
    assert await vector_search(db_session, await query_vector(embedder, "x"), limit=0) == []
