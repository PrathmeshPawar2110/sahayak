"""Deterministic offline embeddings for tests: hashed bag of words, L2-normalised.

Texts that share words get similar vectors, so retrieval tests behave sensibly without a model.
"""

import hashlib
import math
import re
from collections.abc import Sequence

from sahayak.rag.embeddings.base import EmbeddingKind, EmbeddingProvider

_WORD_RE = re.compile(r"\w+", re.UNICODE)


class FakeEmbeddingProvider(EmbeddingProvider):
    name = "fake"

    def __init__(self, dimension: int = 1024) -> None:
        self._dimension = dimension
        self.calls: list[tuple[EmbeddingKind, list[str]]] = []

    @property
    def model(self) -> str:
        return "hashed-bag-of-words"

    @property
    def dimension(self) -> int:
        return self._dimension

    async def _embed(self, texts: Sequence[str], kind: EmbeddingKind) -> list[list[float]]:
        self.calls.append((kind, list(texts)))
        return [self.vector(text) for text in texts]

    def vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dimension
        for word in _WORD_RE.findall(text.lower()):
            bucket = int.from_bytes(hashlib.blake2b(word.encode(), digest_size=4).digest(), "big")
            vector[bucket % self._dimension] += 1.0
        norm = math.sqrt(sum(x * x for x in vector))
        if norm == 0:
            vector[0] = 1.0
            return vector
        return [x / norm for x in vector]
