from redis.asyncio import Redis

from sahayak.rag.embeddings.cache import CachedEmbeddingProvider
from sahayak.rag.embeddings.fake import FakeEmbeddingProvider


async def test_embedding_cache_round_trip_on_real_redis(redis: Redis) -> None:
    inner = FakeEmbeddingProvider()
    cached = CachedEmbeddingProvider(inner, redis, ttl_sec=60)

    first = await cached.embed_queries(["पेंशन योजना"])
    second = await cached.embed_queries(["पेंशन योजना"])

    assert len(inner.calls) == 1
    assert second[0][:5] == first[0][:5]
