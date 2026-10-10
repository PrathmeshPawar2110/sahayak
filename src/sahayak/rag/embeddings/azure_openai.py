"""Azure OpenAI embeddings (text-embedding-3-large shortened to the index dimension)."""

from collections.abc import Sequence

import httpx2
import openai

from sahayak.rag.embeddings.base import EmbeddingError, EmbeddingKind, EmbeddingProvider

MAX_BATCH = 64


class AzureOpenAIEmbeddingProvider(EmbeddingProvider):
    name = "azure_openai"

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        deployment: str,
        api_version: str,
        dimension: int,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self._deployment = deployment
        self._dimension = dimension
        # The SDK's own retries (2, with backoff) cover rate limits and 5xx for embeddings.
        self._client = openai.AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            http_client=http_client,
        )

    @property
    def model(self) -> str:
        return self._deployment

    @property
    def dimension(self) -> int:
        return self._dimension

    async def _embed(self, texts: Sequence[str], kind: EmbeddingKind) -> list[list[float]]:
        # OpenAI embedding models use no query/passage prefixes.
        vectors: list[list[float]] = []
        for start in range(0, len(texts), MAX_BATCH):
            batch = list(texts[start : start + MAX_BATCH])
            try:
                response = await self._client.embeddings.create(
                    model=self._deployment, input=batch, dimensions=self._dimension
                )
            except openai.APIError as exc:
                raise EmbeddingError(
                    f"azure openai embeddings failed: {type(exc).__name__}"
                ) from exc
            vectors.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
        return vectors
