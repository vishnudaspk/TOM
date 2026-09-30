"""CPU-based embedding providers and protocols for TOM memory subsystem.

Adheres to:
- PHASE5_IMPLEMENTATIONPLAN.md (Iteration 2)
- Decision 038: CPU-Only Embeddings & Optional Qdrant Semantic Fallback
- skills/python/memory-system: Embeddings strictly run on CPU without GPU/CUDA overhead.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
from collections import OrderedDict
from typing import Protocol, runtime_checkable

logger = logging.getLogger("tom.memory.embeddings")


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol for embedding generation providers."""

    @property
    def dimension(self) -> int:
        """Embedding vector dimension."""
        ...

    async def embed(self, text: str) -> list[float]:
        """Generate a normalized embedding vector for the provided text."""
        ...

    async def check_health(self) -> bool:
        """Verify the health and readiness of the embedding provider."""
        ...


def _normalize_vector(vec: list[float]) -> list[float]:
    """Normalize a float vector to unit length (L2 norm = 1.0)."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return [0.0] * len(vec)
    return [x / norm for x in vec]


def _deterministic_token_vector(text: str, dimension: int = 384) -> list[float]:
    """Generate a deterministic normalized vector based on tokens and text hashing."""
    vec = [0.0] * dimension
    tokens = re.findall(r"\w+", text.lower())
    if not tokens:
        tokens = ["__empty__"]

    for token in tokens:
        h = hashlib.sha256(token.encode("utf-8")).digest()
        # Spread token hash across dimension buckets
        for i in range(dimension):
            byte_val = h[i % len(h)]
            # Map byte (0..255) to float (-1.0..1.0)
            val = ((byte_val / 255.0) * 2.0) - 1.0
            vec[i] += val

    # Add whole text hash influence
    full_h = hashlib.sha256(text.encode("utf-8")).digest()
    for i in range(dimension):
        byte_val = full_h[i % len(full_h)]
        val = ((byte_val / 255.0) * 2.0) - 1.0
        vec[i] += val * 0.5

    return _normalize_vector(vec)


class MockEmbeddingProvider:
    """Deterministic embedding provider for isolated unit and integration testing."""

    def __init__(self, dimension: int = 384, healthy: bool = True) -> None:
        self._dimension = dimension
        self.healthy = healthy

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, text: str) -> list[float]:
        """Generate a deterministic unit vector."""
        if not self.healthy:
            raise RuntimeError("MockEmbeddingProvider is unhealthy")
        return _deterministic_token_vector(text, self._dimension)

    async def check_health(self) -> bool:
        """Return provider health status."""
        return self.healthy


class CPUEmbeddingProvider:
    """Lightweight, CPU-only embedding provider with LRU caching."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-en-v1.5",
        cache_size: int = 1000,
        dimension: int = 384,
    ) -> None:
        self.model_name = model_name
        self.cache_size = cache_size
        self._dimension = dimension
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._fastembed_model = None
        self._initialized = False

    @property
    def dimension(self) -> int:
        return self._dimension

    def _sync_embed(self, text: str) -> list[float]:
        """Synchronously compute embedding on CPU."""
        if text in self._cache:
            self._cache.move_to_end(text)
            return self._cache[text]

        # Check if fastembed is available
        if not self._initialized:
            self._initialized = True
            try:
                from fastembed import TextEmbedding  # type: ignore

                # Strictly run on CPU (threads=1, no CUDA device)
                self._fastembed_model = TextEmbedding(
                    model_name=self.model_name,
                    threads=1,
                )
                logger.info(
                    "Initialized FastEmbed CPU model: %s",
                    self.model_name,
                )
            except (ImportError, Exception) as err:
                logger.debug(
                    "FastEmbed not available (%s); using CPU token projection.",
                    err,
                )
                self._fastembed_model = None

        if self._fastembed_model is not None:
            try:
                embeddings = list(self._fastembed_model.embed([text]))
                vector = _normalize_vector(embeddings[0].tolist())
            except Exception as err:
                logger.warning(
                    "FastEmbed generation failed (%s); falling back to CPU projection.",
                    err,
                )
                vector = _deterministic_token_vector(text, self._dimension)
        else:
            vector = _deterministic_token_vector(text, self._dimension)

        # LRU cache update
        if len(self._cache) >= self.cache_size:
            self._cache.popitem(last=False)
        self._cache[text] = vector

        return vector

    async def embed(self, text: str) -> list[float]:
        """Asynchronously compute or retrieve cached embedding on CPU."""
        return await asyncio.to_thread(self._sync_embed, text)

    async def check_health(self) -> bool:
        """Check if CPU embedding generation is operational."""
        try:
            vec = await self.embed("health_check")
            return len(vec) == self._dimension
        except Exception:
            return False


__all__ = [
    "CPUEmbeddingProvider",
    "EmbeddingProvider",
    "MockEmbeddingProvider",
]
