"""Redis read-through cache for embeddings (NFR Cost: cache embeddings for repeated queries).

The cache fails open: if Redis is unreachable, vectors are computed as if nothing were cached,
so a cache outage costs latency, not requests.
"""

import hashlib
from collections.abc import Sequence

import numpy as np
import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from sahayak.rag.embeddings.base import EmbeddingKind, EmbeddingProvider

log = structlog.get_logger(__name__)

KEY_VERSION = "v1"


class CachedEmbeddingProvider(EmbeddingProvider):
    """Wraps another provider; only texts missing from Redis reach it."""

    def __init__(self, inner: EmbeddingProvider, redis: Redis, *, ttl_sec: int) -> None:
        self._inner = inner
        self._redis = redis
        self._ttl_sec = ttl_sec
        self.name = inner.name

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def dimension(self) -> int:
        return self._inner.dimension

    def key(self, text: str, kind: EmbeddingKind) -> str:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return f"emb:{KEY_VERSION}:{self._inner.cache_namespace}:{kind}:{digest}"

    async def _embed(self, texts: Sequence[str], kind: EmbeddingKind) -> list[list[float]]:
        keys = [self.key(text, kind) for text in texts]
        try:
            cached: list[bytes | str | None] = await self._redis.mget(keys)
        except RedisError as exc:
            log.warning("embedding_cache_unavailable", error=type(exc).__name__)
            return await self._inner.embed(texts, kind)
        vectors: list[list[float] | None] = [
            _decode(raw, self.dimension) if raw is not None else None for raw in cached
        ]

        missing = [i for i, vector in enumerate(vectors) if vector is None]
        unique_texts = list(dict.fromkeys(texts[i] for i in missing))
        if unique_texts:
            computed = dict(
                zip(unique_texts, await self._inner.embed(unique_texts, kind), strict=True)
            )
            try:
                async with self._redis.pipeline(transaction=False) as pipe:
                    for text, vector in computed.items():
                        pipe.set(self.key(text, kind), _encode(vector), ex=self._ttl_sec)
                    await pipe.execute()
            except RedisError as exc:
                log.warning("embedding_cache_unavailable", error=type(exc).__name__)
            for i in missing:
                vectors[i] = computed[texts[i]]
        return [v for v in vectors if v is not None]


def _encode(vector: list[float]) -> bytes:
    return np.asarray(vector, dtype="<f4").tobytes()


def _decode(raw: bytes | str, dimension: int) -> list[float] | None:
    # Anything that is not exactly `dimension` float32s is stale or corrupt: recompute it.
    if not isinstance(raw, bytes) or len(raw) != dimension * 4:
        return None
    array = np.frombuffer(raw, dtype="<f4")
    return [float(x) for x in array]
