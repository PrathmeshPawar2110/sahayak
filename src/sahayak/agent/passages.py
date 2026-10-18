"""Scheme passages for the agent: shortlisting by need and evidence for replies (TRD §10).

The agent searches with the English search text its turn-understanding call already produced,
so no extra translation call is needed (D-030 allowed for this).
"""

import re
from abc import ABC, abstractmethod
from collections.abc import Collection

from sahayak.agent.catalogue import Catalogue, SessionScope
from sahayak.agent.state import EvidenceChunk
from sahayak.db.models import Language as ChunkLanguage
from sahayak.eligibility.profile import Language
from sahayak.rag.retriever import HybridRetriever
from sahayak.rag.search import SearchFilter

_LANGUAGES: dict[ChunkLanguage, Language] = {
    ChunkLanguage.HI: "hi",
    ChunkLanguage.MR: "mr",
    ChunkLanguage.EN: "en",
}
_WORD_RE = re.compile(r"\w+")


class PassageSearch(ABC):
    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        state: str | None,
        scheme_ids: Collection[str] | None = None,
        top_k: int = 5,
    ) -> list[EvidenceChunk]:
        """Passages for an English `query`, from schemes that may apply in `state`."""


class StaticPassageSearch(PassageSearch):
    """In-memory passages ranked by shared words, for tests and offline runs."""

    def __init__(self, chunks: Collection[EvidenceChunk], catalogue: Catalogue) -> None:
        self._chunks = list(chunks)
        self._catalogue = catalogue

    async def search(
        self,
        query: str,
        *,
        state: str | None,
        scheme_ids: Collection[str] | None = None,
        top_k: int = 5,
    ) -> list[EvidenceChunk]:
        allowed = {scheme.id for scheme in self._catalogue.evaluable(state)}
        if scheme_ids is not None:
            allowed &= set(scheme_ids)
        words = set(_WORD_RE.findall(query.casefold()))
        scored = [
            (len(words & set(_WORD_RE.findall(f"{c.section} {c.text}".casefold()))), i, c)
            for i, c in enumerate(self._chunks)
            if c.scheme_id in allowed
        ]
        ranked = sorted((s for s in scored if s[0] > 0), key=lambda s: (-s[0], s[1]))
        return [chunk for _, _, chunk in ranked[:top_k]]


class RetrieverPassageSearch(PassageSearch):
    """Hybrid vector + full-text retrieval over `scheme_chunks` (M3)."""

    def __init__(self, sessions: SessionScope, retriever: HybridRetriever) -> None:
        self._sessions = sessions
        self._retriever = retriever

    async def search(
        self,
        query: str,
        *,
        state: str | None,
        scheme_ids: Collection[str] | None = None,
        top_k: int = 5,
    ) -> list[EvidenceChunk]:
        if not query.strip() or (scheme_ids is not None and not scheme_ids):
            return []
        async with self._sessions() as session:
            retrieval = await self._retriever.retrieve(
                session,
                query,
                language=ChunkLanguage.EN,
                search_filter=SearchFilter.for_user(state, scheme_ids=scheme_ids),
                top_k=min(top_k, self._retriever.candidates),
            )
        return [
            EvidenceChunk(
                chunk_id=str(chunk.chunk_id),
                scheme_id=chunk.scheme_id,
                source_url=chunk.source_url,
                section=chunk.section,
                language=_LANGUAGES[chunk.language],
                text=chunk.text,
                last_verified_at=chunk.scheme_last_verified_at,
            )
            for chunk in retrieval.chunks
        ]
