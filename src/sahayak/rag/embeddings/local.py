"""Local multilingual embeddings via fastembed (ONNX Runtime, CPU, no torch).

The default model, intfloat/multilingual-e5-large, expects "query: " / "passage: " prefixes and
L2-normalised outputs; fastembed adds neither, so this class does both.
"""

import asyncio
import os
import threading
from collections.abc import Sequence
from typing import Any

import numpy as np

from sahayak.rag.embeddings.base import EmbeddingKind, EmbeddingProvider

E5_PREFIXES: dict[EmbeddingKind, str] = {"query": "query: ", "passage": "passage: "}
KNOWN_DIMENSIONS = {"intfloat/multilingual-e5-large": 1024}


class LocalEmbeddingProvider(EmbeddingProvider):
    name = "local"

    def __init__(
        self, *, model: str, cache_dir: str, dimension: int | None = None, batch_size: int = 16
    ) -> None:
        self._model_name = model
        self._cache_dir = cache_dir
        self._dimension = dimension or KNOWN_DIMENSIONS.get(model, 0)
        if not self._dimension:
            raise ValueError(f"unknown dimension for local model {model!r}; pass dimension=")
        self._batch_size = batch_size
        self._prefixes = E5_PREFIXES if "e5" in model.lower() else {"query": "", "passage": ""}
        self._encoder: Any = None
        self._lock = threading.Lock()

    @property
    def model(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    async def _embed(self, texts: Sequence[str], kind: EmbeddingKind) -> list[list[float]]:
        prefixed = [self._prefixes[kind] + text for text in texts]
        # CPU-bound inference (and a ~2 GB download on first use): keep it off the event loop.
        return await asyncio.to_thread(self._embed_sync, prefixed)

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        matrix = np.asarray(list(self._load().embed(texts, batch_size=self._batch_size)))
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        normalised = matrix / np.where(norms == 0, 1.0, norms)
        return [[float(x) for x in row] for row in normalised]

    def _load(self) -> Any:
        with self._lock:
            if self._encoder is None:
                # Windows without Developer Mode can't symlink the HF cache; copies work fine.
                os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
                from fastembed import TextEmbedding  # heavy import, deferred to first use

                self._encoder = TextEmbedding(self._model_name, cache_dir=self._cache_dir)
            return self._encoder
