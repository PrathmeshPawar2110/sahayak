import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.rag.embeddings.fake import FakeEmbeddingProvider
from sahayak.rag.retriever import HybridRetriever
from sahayak.rag.search import SearchFilter
from tests.integration.rag_corpus import load_corpus

pytestmark = pytest.mark.usefixtures("corpus")


@pytest.fixture
def embedder() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider()


@pytest.fixture
async def corpus(db_session: AsyncSession, embedder: FakeEmbeddingProvider) -> None:
    await load_corpus(db_session, embedder)
    embedder.calls.clear()


async def test_retrieve_fuses_both_searches_into_top_k(
    db_session: AsyncSession, embedder: FakeEmbeddingProvider
) -> None:
    retriever = HybridRetriever(embedder)

    retrieval = await retriever.retrieve(db_session, "monthly pension for unorganised workers")

    assert retrieval.queries == ["monthly pension for unorganised workers"]
    chunks = retrieval.chunks
    assert len(chunks) == 5
    assert len({c.chunk_id for c in chunks}) == 5
    assert (chunks[0].scheme_id, chunks[0].section) == ("worker-pension", "Benefits")
    assert set(chunks[0].ranks) == {"vector:0", "text:0"}
    assert [c.score for c in chunks] == sorted((c.score for c in chunks), reverse=True)
    assert embedder.calls == [("query", ["monthly pension for unorganised workers"])]


async def test_retrieve_applies_the_filter_to_both_searches(
    db_session: AsyncSession, embedder: FakeEmbeddingProvider
) -> None:
    retriever = HybridRetriever(embedder)

    retrieval = await retriever.retrieve(
        db_session,
        "women rupees monthly support housing families",
        search_filter=SearchFilter.for_user("KA"),
        top_k=10,
    )

    assert "mh-women" not in {c.scheme_id for c in retrieval.chunks}
    assert "ka-housing" in {c.scheme_id for c in retrieval.chunks}
    assert {c.scheme_id for c in retrieval.chunks} <= {
        "farmer-support",
        "worker-pension",
        "ka-housing",
    }


async def test_retrieve_restricted_to_one_scheme(
    db_session: AsyncSession, embedder: FakeEmbeddingProvider
) -> None:
    retriever = HybridRetriever(embedder)

    retrieval = await retriever.retrieve(
        db_session,
        "how much money do farmers get",
        search_filter=SearchFilter.for_user(scheme_ids=["farmer-support"]),
    )

    assert {c.scheme_id for c in retrieval.chunks} == {"farmer-support"}
    assert len(retrieval.chunks) == 3  # every chunk of the scheme, found by vector search


async def test_blank_query_retrieves_nothing_without_embedding(
    db_session: AsyncSession, embedder: FakeEmbeddingProvider
) -> None:
    retrieval = await HybridRetriever(embedder).retrieve(db_session, "  ")

    assert retrieval.chunks == []
    assert retrieval.queries == []
    assert embedder.calls == []
