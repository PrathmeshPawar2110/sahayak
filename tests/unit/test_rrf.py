import uuid

import pytest

from sahayak.db.models import Language
from sahayak.rag.embeddings.fake import FakeEmbeddingProvider
from sahayak.rag.retriever import HybridRetriever, RetrievedChunk, reciprocal_rank_fusion
from sahayak.rag.search import SearchHit


def hit(name: str, score: float = 0.5) -> SearchHit:
    return SearchHit(
        chunk_id=uuid.uuid5(uuid.NAMESPACE_DNS, name),
        scheme_id=f"scheme-{name}",
        source_url=f"https://example.gov.in/{name}",
        section="Benefits",
        language=Language.EN,
        text=f"text of {name}",
        scheme_last_verified_at=None,
        score=score,
    )


def names(chunks: list[RetrievedChunk]) -> list[str]:
    return [c.scheme_id.removeprefix("scheme-") for c in chunks]


def test_chunk_found_by_both_searches_ranks_first() -> None:
    fused = reciprocal_rank_fusion(
        {"vector": [hit("a"), hit("b"), hit("c")], "text": [hit("d"), hit("e"), hit("c")]}
    )

    assert names(fused) == ["c", "a", "d", "b", "e"]
    assert fused[0].score == pytest.approx(2 / 63)
    assert fused[0].ranks == {"vector": 3, "text": 3}
    assert fused[1].score == pytest.approx(1 / 61)
    assert fused[1].ranks == {"vector": 1}


def test_fused_chunk_keeps_its_fields() -> None:
    [fused] = reciprocal_rank_fusion({"vector": [hit("a", 0.9)]})
    original = hit("a")

    assert fused.chunk_id == original.chunk_id
    assert (fused.scheme_id, fused.source_url, fused.section, fused.text) == (
        original.scheme_id,
        original.source_url,
        original.section,
        original.text,
    )
    assert fused.language is Language.EN


def test_duplicate_in_one_list_counts_once_at_its_best_rank() -> None:
    fused = reciprocal_rank_fusion({"vector": [hit("a"), hit("b"), hit("a")]})

    assert names(fused) == ["a", "b"]
    assert fused[0].ranks == {"vector": 1}
    assert fused[0].score == pytest.approx(1 / 61)


def test_limit_truncates_after_fusion() -> None:
    lists = {"vector": [hit(n) for n in "abcdefg"]}

    assert names(reciprocal_rank_fusion(lists)) == list("abcde")  # TRD top-k = 5
    assert names(reciprocal_rank_fusion(lists, limit=2)) == ["a", "b"]
    assert reciprocal_rank_fusion(lists, limit=0) == []


def test_ties_are_broken_by_best_rank_then_chunk_id() -> None:
    # x and y are each first in one list: equal scores and equal best ranks.
    fused = reciprocal_rank_fusion({"vector": [hit("x")], "text": [hit("y")]})
    reversed_input = reciprocal_rank_fusion({"text": [hit("y")], "vector": [hit("x")]})

    expected = sorted(["x", "y"], key=lambda n: str(hit(n).chunk_id))
    assert names(fused) == names(reversed_input) == expected


def test_smaller_k_rewards_top_ranks_more() -> None:
    lists = {"vector": [hit("a"), hit("b")], "text": [hit("c"), hit("b")]}

    # b is second in both lists. With a large k, being found twice dominates; with k=0, a and c
    # (1/1 each) tie with b (1/2 + 1/2) and win on their better best rank.
    assert names(reciprocal_rank_fusion(lists, k=1000))[0] == "b"
    assert names(reciprocal_rank_fusion(lists, k=0))[0] != "b"


def test_empty_input_fuses_to_nothing() -> None:
    assert reciprocal_rank_fusion({}) == []
    assert reciprocal_rank_fusion({"vector": [], "text": []}) == []


def test_negative_k_is_rejected() -> None:
    with pytest.raises(ValueError, match="k must be"):
        reciprocal_rank_fusion({"vector": [hit("a")]}, k=-1)


@pytest.mark.parametrize(("top_k", "candidates"), [(0, 20), (6, 5), (-1, 20)])
def test_retriever_rejects_bad_sizes(top_k: int, candidates: int) -> None:
    with pytest.raises(ValueError, match="top_k"):
        HybridRetriever(FakeEmbeddingProvider(), top_k=top_k, candidates=candidates)
