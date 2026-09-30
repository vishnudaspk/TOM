"""Unit tests for CPU-based embedding providers and protocols.

Adheres to:
- PHASE5_IMPLEMENTATIONPLAN.md (Iteration 2)
- Decision 038: CPU-Only Embeddings & Optional Qdrant Semantic Fallback
- skills/python/memory-system
"""

import asyncio
import math

import pytest
from tom.memory.embeddings import (
    CPUEmbeddingProvider,
    EmbeddingProvider,
    MockEmbeddingProvider,
)


def _compute_l2_norm(vec: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vec))


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    return sum(a * b for a, b in zip(vec_a, vec_b, strict=False))


def test_mock_embedding_provider_invariants():
    """Verify MockEmbeddingProvider dimension, determinism, and normalization."""

    async def _test():
        provider = MockEmbeddingProvider(dimension=384)
        assert isinstance(provider, EmbeddingProvider)
        assert provider.dimension == 384
        assert await provider.check_health() is True

        # Determinism
        vec1 = await provider.embed("User prefers dark mode.")
        vec2 = await provider.embed("User prefers dark mode.")
        assert len(vec1) == 384
        assert vec1 == vec2

        # L2 Unit normalization
        norm = _compute_l2_norm(vec1)
        assert pytest.approx(norm, rel=1e-5) == 1.0

        # Distinct content produces distinct vector
        vec3 = await provider.embed("System kernel updated.")
        assert vec1 != vec3

        # Semantic similarity: related strings share higher dot product than unrelated
        vec_related = await provider.embed("User likes dark theme mode.")
        sim_related = _cosine_similarity(vec1, vec_related)
        sim_unrelated = _cosine_similarity(vec1, vec3)
        assert sim_related > sim_unrelated

    asyncio.run(_test())


def test_mock_embedding_unhealthy_state():
    """Verify unhealthy MockEmbeddingProvider raises RuntimeError on embed."""

    async def _test():
        provider = MockEmbeddingProvider(healthy=False)
        assert await provider.check_health() is False

        with pytest.raises(RuntimeError, match="unhealthy"):
            await provider.embed("test")

    asyncio.run(_test())


def test_cpu_embedding_provider_caching_and_execution():
    """Verify CPUEmbeddingProvider caching and strict CPU operation."""

    async def _test():
        provider = CPUEmbeddingProvider(cache_size=2, dimension=384)
        assert isinstance(provider, EmbeddingProvider)
        assert provider.dimension == 384
        assert await provider.check_health() is True

        # Generation and unit normalization
        v1 = await provider.embed("First unique sentence.")
        assert len(v1) == 384
        assert pytest.approx(_compute_l2_norm(v1), rel=1e-5) == 1.0

        # Cache hit check
        assert "First unique sentence." in provider._cache
        v1_cached = await provider.embed("First unique sentence.")
        assert v1 == v1_cached

        # Fill cache to test LRU eviction
        await provider.embed("Second sentence.")
        await provider.embed("Third sentence.")  # Evicts first sentence

        assert "First unique sentence." not in provider._cache
        assert "Second sentence." in provider._cache
        assert "Third sentence." in provider._cache

    asyncio.run(_test())


def test_cpu_embedding_empty_and_whitespace():
    """Verify empty or whitespace strings generate valid unit vectors without crashing."""

    async def _test():
        provider = CPUEmbeddingProvider()
        vec = await provider.embed("   ")
        assert len(vec) == 384
        assert pytest.approx(_compute_l2_norm(vec), rel=1e-5) == 1.0

    asyncio.run(_test())
