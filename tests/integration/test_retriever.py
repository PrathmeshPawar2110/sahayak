import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.db.models import Language
from sahayak.llm.base import LLMRefusalError, LLMTransientError
from sahayak.rag.embeddings.fake import FakeEmbeddingProvider
from sahayak.rag.retriever import HybridRetriever
from sahayak.rag.search import SearchFilter
from sahayak.rag.translation import QueryTranslator
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


class ScriptedTranslator(QueryTranslator):
    def __init__(self, reply: str | Exception | None) -> None:
        self.reply = reply
        self.calls: list[tuple[str, Language | None]] = []

    async def to_english(self, query: str, language: Language | None) -> str | None:
        self.calls.append((query, language))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


async def test_hindi_query_is_also_searched_in_english(
    db_session: AsyncSession, embedder: FakeEmbeddingProvider
) -> None:
    translator = ScriptedTranslator("monthly pension for unorganised workers")
    retriever = HybridRetriever(embedder, translator=translator)

    retrieval = await retriever.retrieve(
        db_session, "असंगठित  मजदूरों के लिए पेंशन", language=Language.HI
    )

    assert translator.calls == [("असंगठित मजदूरों के लिए पेंशन", Language.HI)]
    assert retrieval.queries == [
        "असंगठित मजदूरों के लिए पेंशन",
        "monthly pension for unorganised workers",
    ]
    # Both queries are embedded in one call; the English chunk is found through the translation.
    assert embedder.calls == [("query", retrieval.queries)]
    top = retrieval.chunks[0]
    assert (top.scheme_id, top.section) == ("worker-pension", "Benefits")
    assert {"vector:1", "text:1"} <= set(top.ranks)


async def test_english_query_is_not_translated(
    db_session: AsyncSession, embedder: FakeEmbeddingProvider
) -> None:
    translator = ScriptedTranslator("unused")

    retrieval = await HybridRetriever(embedder, translator=translator).retrieve(
        db_session, "pension", language=Language.EN
    )

    assert translator.calls == []
    assert retrieval.queries == ["pension"]


@pytest.mark.parametrize("reply", [None, LLMTransientError("timeout"), LLMRefusalError("no")])
async def test_failed_or_empty_translation_searches_the_original_only(
    db_session: AsyncSession,
    embedder: FakeEmbeddingProvider,
    reply: str | Exception | None,
) -> None:
    retriever = HybridRetriever(embedder, translator=ScriptedTranslator(reply))

    retrieval = await retriever.retrieve(db_session, "किसान परिवार", language=Language.HI)

    assert retrieval.queries == ["किसान परिवार"]
    assert retrieval.chunks[0].language is Language.HI
    assert set(retrieval.chunks[0].ranks) == {"vector:0", "text:0"}
