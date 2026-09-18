"""TOM models subsystem.

Exposes the decoupled language model provider abstraction (LLMProvider),
OpenAI-compatible HTTP provider (HttpModelProvider / LMStudioProvider),
deterministic test double (MockModelProvider), and all typed schemas.

Architecture (Decision 033):
    Agent
      ↓
    LLMProvider  ← abstract boundary
      ↓
    HttpModelProvider / LMStudioProvider
      ↓
    http://127.0.0.1:1234/v1  (LM Studio / Bionic / any OpenAI-compatible endpoint)
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
from tom.models.providers.http import HttpModelProvider, LMStudioProvider
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
    "LMStudioProvider",
    "MockModelProvider",
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
