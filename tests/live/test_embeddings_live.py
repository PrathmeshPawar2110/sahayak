"""Runs the real local embedding model (downloads ~2 GB on first run). `uv run pytest -m live`."""

import pytest

from sahayak.config import Settings
from sahayak.rag.embeddings.local import LocalEmbeddingProvider

pytestmark = pytest.mark.live


async def test_e5_places_hindi_marathi_and_english_in_one_space() -> None:
    settings = Settings()
    provider = LocalEmbeddingProvider(
        model=settings.embedding_model, cache_dir=settings.local_model_dir
    )

    [query] = await provider.embed_queries(["pension scheme for farmers"])
    hindi, marathi, unrelated = await provider.embed_passages(
        [
            "किसानों के लिए पेंशन योजना",
            "शेतकऱ्यांसाठी निवृत्तिवेतन योजना",
            "girl child savings account interest rate",
        ]
    )

    def cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert cosine(query, hindi) > cosine(query, unrelated)
    assert cosine(query, marathi) > cosine(query, unrelated)
