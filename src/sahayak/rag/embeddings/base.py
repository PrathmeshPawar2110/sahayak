"""Provider-agnostic text embeddings (TRD §7: hi/mr/en in one shared vector space).

Retrieval models such as e5 embed queries and passages differently, so callers always say which
one they are embedding.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Literal

EmbeddingKind = Literal["query", "passage"]


class EmbeddingError(Exception):
    """Embedding failed or returned vectors of the wrong shape."""


class EmbeddingProvider(ABC):
    name: str

    @property
    @abstractmethod
    def model(self) -> str: ...

    @property
    @abstractmethod
    def dimension(self) -> int: ...

    @property
    def cache_namespace(self) -> str:
        """Identifies the vector space: vectors are only comparable within one namespace."""
        return f"{self.name}:{self.model}:{self.dimension}"

    async def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return await self.embed(texts, "passage")

    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return await self.embed(texts, "query")

    async def embed(self, texts: Sequence[str], kind: EmbeddingKind) -> list[list[float]]:
        if not texts:
            return []
        vectors = await self._embed(texts, kind)
        if len(vectors) != len(texts):
            raise EmbeddingError(f"{self.name}: got {len(vectors)} vectors for {len(texts)} texts")
        if any(len(vector) != self.dimension for vector in vectors):
            raise EmbeddingError(f"{self.name}: expected {self.dimension}-dimensional vectors")
        return vectors

    @abstractmethod
    async def _embed(self, texts: Sequence[str], kind: EmbeddingKind) -> list[list[float]]: ...
