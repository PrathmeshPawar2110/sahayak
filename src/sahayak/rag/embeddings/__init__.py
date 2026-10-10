"""Embedding providers. Use `create_embedding_provider`; vendor SDKs load only when chosen."""

from redis.asyncio import Redis

from sahayak.config import Settings
from sahayak.db.models import EMBEDDING_DIM
from sahayak.rag.embeddings.base import EmbeddingError, EmbeddingKind, EmbeddingProvider
from sahayak.rag.embeddings.cache import CachedEmbeddingProvider

__all__ = [
    "EmbeddingError",
    "EmbeddingKind",
    "EmbeddingProvider",
    "create_embedding_provider",
]


def create_embedding_provider(settings: Settings, redis: Redis | None = None) -> EmbeddingProvider:
    """Build the configured provider, wrapped in the Redis cache when a client is given."""
    provider: EmbeddingProvider
    if settings.embedding_provider == "azure_openai":
        if not settings.azure_openai_embedding_deployment:
            raise EmbeddingError(
                "EMBEDDING_PROVIDER=azure_openai needs AZURE_OPENAI_EMBEDDING_DEPLOYMENT"
            )
        from sahayak.rag.embeddings.azure_openai import AzureOpenAIEmbeddingProvider

        provider = AzureOpenAIEmbeddingProvider(
            endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key.get_secret_value(),
            deployment=settings.azure_openai_embedding_deployment,
            api_version=settings.azure_openai_api_version,
            dimension=EMBEDDING_DIM,
        )
    else:
        from sahayak.rag.embeddings.local import LocalEmbeddingProvider

        provider = LocalEmbeddingProvider(
            model=settings.embedding_model, cache_dir=settings.local_model_dir
        )
    if provider.dimension != EMBEDDING_DIM:
        raise EmbeddingError(
            f"{provider.model} produces {provider.dimension}-d vectors; the index needs "
            f"{EMBEDDING_DIM}"
        )
    if redis is None:
        return provider
    return CachedEmbeddingProvider(
        provider, redis, ttl_sec=settings.embedding_cache_ttl_days * 86_400
    )
