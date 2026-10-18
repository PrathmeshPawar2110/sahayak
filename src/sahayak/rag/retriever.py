"""Hybrid retrieval: vector and full-text search fused with Reciprocal Rank Fusion (TRD §7, §10).

RRF scores a chunk by its rank in each list, `sum(1 / (k + rank))`, so the two searches' scores,
which aren't comparable, never need calibrating against each other. A chunk found by both
searches beats one found by either alone at a similar rank.
"""

import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

import structlog
from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession

from sahayak.db.models import Language
from sahayak.rag.embeddings import EmbeddingProvider
from sahayak.rag.search import NO_FILTER, SearchFilter, SearchHit, fulltext_search, vector_search

log = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)

# The constant from Cormack, Clarke & Buettcher (2009); it damps the weight of the very top ranks.
RRF_K = 60
TOP_K = 5  # TRD §10 retrieve_evidence
CANDIDATES = 20  # hits taken from each search before fusion


@dataclass(frozen=True)
class RetrievedChunk:
    """A fused result. `ranks` maps each search that found the chunk to its 1-based rank there."""

    chunk_id: uuid.UUID
    scheme_id: str
    source_url: str
    section: str
    language: Language
    text: str
    scheme_last_verified_at: datetime | None
    score: float
    ranks: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Retrieval:
    chunks: list[RetrievedChunk]
    # Every query text that was searched: the user's own, plus any translation (M3-T04).
    queries: list[str]


def reciprocal_rank_fusion(
    ranked_lists: Mapping[str, Sequence[SearchHit]], *, k: int = RRF_K, limit: int = TOP_K
) -> list[RetrievedChunk]:
    """Fuse named ranked lists into one, deduplicated by chunk id.

    A chunk listed twice in the same list counts once, at its best rank. Ties go to the chunk
    with the better best rank, then to the chunk id, so the order is deterministic.
    """
    if k < 0:
        raise ValueError("k must be >= 0")
    hits: dict[uuid.UUID, SearchHit] = {}
    ranks: dict[uuid.UUID, dict[str, int]] = {}
    for name, hit_list in ranked_lists.items():
        for rank, hit in enumerate(hit_list, start=1):
            hits.setdefault(hit.chunk_id, hit)
            ranks.setdefault(hit.chunk_id, {}).setdefault(name, rank)

    def score(chunk_id: uuid.UUID) -> float:
        return sum(1.0 / (k + rank) for rank in ranks[chunk_id].values())

    order = sorted(hits, key=lambda c: (-score(c), min(ranks[c].values()), str(c)))
    return [
        RetrievedChunk(
            chunk_id=chunk_id,
            scheme_id=hits[chunk_id].scheme_id,
            source_url=hits[chunk_id].source_url,
            section=hits[chunk_id].section,
            language=hits[chunk_id].language,
            text=hits[chunk_id].text,
            scheme_last_verified_at=hits[chunk_id].scheme_last_verified_at,
            score=score(chunk_id),
            ranks=ranks[chunk_id],
        )
        for chunk_id in order[: max(limit, 0)]
    ]


class HybridRetriever:
    """Vector + full-text search over the scheme chunks, fused with RRF."""

    def __init__(
        self,
        embedder: EmbeddingProvider,
        *,
        top_k: int = TOP_K,
        candidates: int = CANDIDATES,
        rrf_k: int = RRF_K,
    ) -> None:
        if top_k <= 0 or candidates < top_k:
            raise ValueError("need 0 < top_k <= candidates")
        self._embedder = embedder
        self.top_k = top_k
        self.candidates = candidates
        self.rrf_k = rrf_k

    async def retrieve(
        self,
        session: AsyncSession,
        query: str,
        *,
        search_filter: SearchFilter = NO_FILTER,
        top_k: int | None = None,
    ) -> Retrieval:
        queries = [query] if query.strip() else []
        with tracer.start_as_current_span("rag.retrieve") as span:
            started = time.perf_counter()
            ranked = await self._search(session, queries, search_filter)
            chunks = reciprocal_rank_fusion(ranked, k=self.rrf_k, limit=top_k or self.top_k)
            latency_ms = round((time.perf_counter() - started) * 1000)
            span.set_attribute("rag.queries", len(queries))
            span.set_attribute("rag.results", len(chunks))
            # Query text can hold personal details: log only counts and scheme ids.
            log.info(
                "retrieval",
                queries=len(queries),
                hits={name: len(hits) for name, hits in ranked.items()},
                results=len(chunks),
                schemes=[c.scheme_id for c in chunks],
                latency_ms=latency_ms,
            )
        return Retrieval(chunks=chunks, queries=queries)

    async def _search(
        self, session: AsyncSession, queries: Sequence[str], search_filter: SearchFilter
    ) -> dict[str, list[SearchHit]]:
        """One vector and one full-text list per query, named `vector:<i>` / `text:<i>`."""
        ranked: dict[str, list[SearchHit]] = {}
        vectors = await self._embedder.embed_queries(queries)
        for i, (query, vector) in enumerate(zip(queries, vectors, strict=True)):
            ranked[f"vector:{i}"] = await vector_search(
                session, vector, search_filter=search_filter, limit=self.candidates
            )
            ranked[f"text:{i}"] = await fulltext_search(
                session, query, search_filter=search_filter, limit=self.candidates
            )
        return ranked
