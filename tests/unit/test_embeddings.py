import json
import math
from collections.abc import Iterable, Sequence
from typing import Any

import fakeredis
import httpx2
import pytest

from sahayak.config import Settings
from sahayak.rag.embeddings import EmbeddingError, create_embedding_provider
from sahayak.rag.embeddings.azure_openai import AzureOpenAIEmbeddingProvider
from sahayak.rag.embeddings.base import EmbeddingKind, EmbeddingProvider
from sahayak.rag.embeddings.cache import CachedEmbeddingProvider
from sahayak.rag.embeddings.fake import FakeEmbeddingProvider
from sahayak.rag.embeddings.local import LocalEmbeddingProvider


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


class StubEncoder:
    """Stands in for fastembed's TextEmbedding: returns unnormalised vectors."""

    def __init__(self, dimension: int) -> None:
        self.dimension = dimension
        self.seen: list[str] = []

    def embed(self, texts: list[str], batch_size: int) -> Iterable[list[float]]:
        self.seen.extend(texts)
        return [[3.0, 4.0] + [0.0] * (self.dimension - 2) for _ in texts]


@pytest.fixture
def local() -> tuple[LocalEmbeddingProvider, StubEncoder]:
    provider = LocalEmbeddingProvider(model="intfloat/multilingual-e5-large", cache_dir="unused")
    encoder = StubEncoder(provider.dimension)
    provider._encoder = encoder
    return provider, encoder


async def test_local_e5_adds_query_and_passage_prefixes(
    local: tuple[LocalEmbeddingProvider, StubEncoder],
) -> None:
    provider, encoder = local

    await provider.embed_queries(["pension for farmers"])
    await provider.embed_passages(["किसानों को पेंशन"])

    assert encoder.seen == ["query: pension for farmers", "passage: किसानों को पेंशन"]


async def test_local_vectors_are_l2_normalised(
    local: tuple[LocalEmbeddingProvider, StubEncoder],
) -> None:
    provider, _ = local

    [vector] = await provider.embed_passages(["text"])

    assert vector[:2] == pytest.approx([0.6, 0.8])
    assert math.isclose(sum(x * x for x in vector), 1.0)


def test_local_non_e5_models_get_no_prefix_and_need_a_dimension() -> None:
    with pytest.raises(ValueError, match="unknown dimension"):
        LocalEmbeddingProvider(model="some/other-model", cache_dir="unused")

    provider = LocalEmbeddingProvider(model="some/other-model", cache_dir="x", dimension=8)

    assert provider._prefixes == {"query": "", "passage": ""}


class WrongShape(FakeEmbeddingProvider):
    async def _embed(self, texts: Sequence[str], kind: EmbeddingKind) -> list[list[float]]:
        return [[1.0, 0.0]]


async def test_vectors_of_the_wrong_shape_are_rejected() -> None:
    with pytest.raises(EmbeddingError, match="1024-dimensional"):
        await WrongShape().embed_passages(["a"])
    with pytest.raises(EmbeddingError, match="1 vectors for 2 texts"):
        await WrongShape().embed_passages(["a", "b"])


async def test_empty_input_makes_no_call() -> None:
    fake = FakeEmbeddingProvider()

    assert await fake.embed_queries([]) == []
    assert fake.calls == []


async def test_fake_vectors_reflect_shared_words() -> None:
    fake = FakeEmbeddingProvider()

    query, close, far = await fake.embed_queries(
        ["pension for farmers", "monthly pension for small farmers", "girl child savings account"]
    )

    assert cosine(query, close) > cosine(query, far)


# ---------------------------------------------------------------- Azure OpenAI


def azure_provider(handler: Any, dimension: int = 4) -> AzureOpenAIEmbeddingProvider:
    return AzureOpenAIEmbeddingProvider(
        endpoint="https://example.openai.azure.com",
        api_key="test-key",
        deployment="text-embedding-3-large",
        api_version="2024-10-21",
        dimension=dimension,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


async def test_azure_batches_requests_and_keeps_input_order() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        requests.append(body)
        # Return items in reverse order: the provider must sort by index.
        data = [
            {"object": "embedding", "index": i, "embedding": [float(len(text)), 0.0, 0.0, 0.0]}
            for i, text in enumerate(body["input"])
        ][::-1]
        return httpx2.Response(
            200,
            json={
                "object": "list",
                "data": data,
                "model": "text-embedding-3-large",
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    texts = ["x" * n for n in range(1, 71)]
    vectors = await azure_provider(handler).embed_passages(texts)

    assert [v[0] for v in vectors] == [float(n) for n in range(1, 71)]
    assert [len(r["input"]) for r in requests] == [64, 6]
    assert requests[0]["dimensions"] == 4


async def test_azure_errors_become_embedding_errors() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, json={"error": {"message": "bad"}})

    with pytest.raises(EmbeddingError):
        await azure_provider(handler).embed_queries(["q"])


# ---------------------------------------------------------------- Redis cache


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis()


async def test_cache_serves_repeated_texts_without_calling_the_provider(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    inner = FakeEmbeddingProvider()
    cached = CachedEmbeddingProvider(inner, redis, ttl_sec=60)

    first = await cached.embed_queries(["pension", "housing"])
    second = await cached.embed_queries(["housing", "pension", "pension"])

    assert inner.calls == [("query", ["pension", "housing"])]
    assert second[0] == pytest.approx(first[1], abs=1e-6)
    assert second[1] == pytest.approx(first[0], abs=1e-6)
    assert second[2] == pytest.approx(first[0], abs=1e-6)


async def test_cache_only_computes_misses_once_each(redis: fakeredis.FakeAsyncRedis) -> None:
    inner = FakeEmbeddingProvider()
    cached = CachedEmbeddingProvider(inner, redis, ttl_sec=60)
    await cached.embed_passages(["a"])

    await cached.embed_passages(["a", "b", "b", "c"])

    assert inner.calls[-1] == ("passage", ["b", "c"])


async def test_queries_and_passages_are_cached_separately(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    inner = FakeEmbeddingProvider()
    cached = CachedEmbeddingProvider(inner, redis, ttl_sec=60)

    await cached.embed_queries(["same text"])
    await cached.embed_passages(["same text"])

    assert [kind for kind, _ in inner.calls] == ["query", "passage"]


async def test_cache_entries_expire_and_corrupt_entries_are_recomputed(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    inner = FakeEmbeddingProvider()
    cached = CachedEmbeddingProvider(inner, redis, ttl_sec=3600)
    await cached.embed_queries(["q"])
    key = cached.key("q", "query")

    assert 0 < await redis.ttl(key) <= 3600

    await redis.set(key, b"garbage")
    [vector] = await cached.embed_queries(["q"])

    assert len(inner.calls) == 2
    assert vector == pytest.approx(inner.vector("q"), abs=1e-6)


def test_cache_keys_are_namespaced_by_provider_and_model(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    cached = CachedEmbeddingProvider(FakeEmbeddingProvider(), redis, ttl_sec=60)

    assert cached.key("q", "query").startswith("emb:v1:fake:hashed-bag-of-words:1024:query:")


# ---------------------------------------------------------------- factory


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_factory_defaults_to_local_e5(redis: fakeredis.FakeAsyncRedis) -> None:
    plain = create_embedding_provider(make_settings())
    cached = create_embedding_provider(make_settings(), redis)

    assert isinstance(plain, LocalEmbeddingProvider)
    assert plain.model == "intfloat/multilingual-e5-large"
    assert isinstance(cached, CachedEmbeddingProvider)


def test_factory_builds_azure_with_the_index_dimension() -> None:
    provider = create_embedding_provider(
        make_settings(
            embedding_provider="azure_openai",
            azure_openai_endpoint="https://x.openai.azure.com",
            azure_openai_api_key="k",
            azure_openai_embedding_deployment="te3l",
        )
    )

    assert isinstance(provider, EmbeddingProvider)
    assert provider.dimension == 1024


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"embedding_provider": "azure_openai"}, "AZURE_OPENAI_EMBEDDING_DEPLOYMENT"),
        ({"embedding_model": "some/unknown-model"}, "unknown dimension"),
    ],
)
def test_factory_rejects_unusable_config(overrides: dict[str, object], error: str) -> None:
    with pytest.raises((EmbeddingError, ValueError), match=error):
        create_embedding_provider(make_settings(**overrides))
