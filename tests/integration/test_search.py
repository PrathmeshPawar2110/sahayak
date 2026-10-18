import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.db.models import SchemeStatus
from sahayak.rag.embeddings.fake import FakeEmbeddingProvider
from sahayak.rag.search import SearchFilter, fulltext_search, vector_search
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


async def test_fulltext_search_stems_english_and_ranks_more_matched_terms_first(
    db_session: AsyncSession,
) -> None:
    hits = await fulltext_search(db_session, "Which farmer families are covered?")

    # "farmer" also matches "Farmers" (stemmed); the chunk with both terms ranks first.
    assert (hits[0].scheme_id, hits[0].section) == ("farmer-support", "Eligibility")
    assert {(h.scheme_id, h.section) for h in hits[1:]} == {
        ("farmer-support", "Benefits"),
        ("ka-housing", "Benefits"),  # "families"
    }
    assert hits[0].score > max(h.score for h in hits[1:]) > 0


async def test_fulltext_search_matches_any_term(db_session: AsyncSession) -> None:
    hits = await fulltext_search(db_session, "pension or housing")

    assert {h.scheme_id for h in hits} == {"worker-pension", "ka-housing"}


async def test_fulltext_search_matches_hindi_and_marathi_word_forms(
    db_session: AsyncSession,
) -> None:
    hindi = await fulltext_search(db_session, "किसान परिवार")
    marathi = await fulltext_search(db_session, "महिलांना किती पैसे मिळतात")

    assert [(h.scheme_id, h.language.value) for h in hindi] == [("farmer-support", "hi")]
    assert [(h.scheme_id, h.language.value) for h in marathi] == [("mh-women", "mr")]


async def test_fulltext_search_matches_numbers_across_languages(db_session: AsyncSession) -> None:
    hits = await fulltext_search(db_session, "हर साल 6000 रुपये")

    # The Hindi chunk shares the words, the English one the amount, the Marathi one "रुपये".
    assert hits[0].language.value == "hi"
    assert {(h.scheme_id, h.language.value) for h in hits} == {
        ("farmer-support", "hi"),
        ("farmer-support", "en"),
        ("mh-women", "mr"),
    }


async def test_fulltext_search_applies_the_filter(db_session: AsyncSession) -> None:
    default = await fulltext_search(db_session, "farmers loan waiver press notice")
    in_karnataka = await fulltext_search(
        db_session, "women rupees", search_filter=SearchFilter.for_user("KA")
    )

    assert {h.scheme_id for h in default} == {"farmer-support"}
    assert {h.scheme_id for h in in_karnataka} == {"farmer-support"}


@pytest.mark.parametrize("query", ["", "   ", "the of and", "को"])
async def test_fulltext_search_without_searchable_terms_finds_nothing(
    db_session: AsyncSession, query: str
) -> None:
    # "को" is a word to `simple`, but no chunk text besides the Hindi one has it.
    hits = await fulltext_search(db_session, query)

    assert all(h.language.value == "hi" for h in hits)
    if query != "को":
        assert hits == []


async def test_fulltext_search_treats_query_syntax_as_text(db_session: AsyncSession) -> None:
    hits = await fulltext_search(db_session, "farmer's & 'pension' | !(housing) <-> :*")

    assert {h.scheme_id for h in hits} == {"farmer-support", "worker-pension", "ka-housing"}


async def test_fulltext_search_with_no_limit_returns_nothing(db_session: AsyncSession) -> None:
    assert await fulltext_search(db_session, "farmers", limit=0) == []
