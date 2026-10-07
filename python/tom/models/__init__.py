"""TOM models subsystem.

Exposes the decoupled language model provider abstraction (LLMProvider),
OpenAI-compatible HTTP provider (HttpModelProvider), deterministic test double
(MockModelProvider), benchmark suite, and typed schemas.

Architecture (Decision 033):
    Agent
      ↓
    LLMProvider  ← abstract boundary
      ↓
    HttpModelProvider
      ↓
    llama.cpp OpenAI-compatible endpoint (http://127.0.0.1:65400/v1)
"""

from tom.models.base import (
    LLMProvider,
    ModelAPIError,
    ModelConnectionError,
    ModelProvider,
    ModelProviderError,
    ModelResponseError,
    ModelTimeoutError,
)
from tom.models.benchmark import (
    BenchmarkMetric,
    BenchmarkResult,
    IterationResult,
    ModelBenchmarkSuite,
)
from tom.models.providers.http import HttpModelProvider
from tom.models.providers.mock import MockModelProvider
from tom.schemas.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    StreamChunk,
    TokenUsage,
    ToolCallRequest,
)

__all__ = [
    # Abstract provider interface
    "LLMProvider",
    "ModelProvider",
    # Concrete providers
    "HttpModelProvider",
    "MockModelProvider",
    # Benchmark suite
    "BenchmarkMetric",
    "BenchmarkResult",
    "IterationResult",
    "ModelBenchmarkSuite",
    # Error hierarchy
    "ModelProviderError",
    "ModelConnectionError",
    "ModelAPIError",
    "ModelTimeoutError",
    "ModelResponseError",
    # Schemas
    "FinishReason",
    "ModelRequest",
    "ModelResponse",
    "StreamChunk",
    "TokenUsage",
    "ToolCallRequest",
]
