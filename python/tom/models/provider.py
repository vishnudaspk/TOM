"""TOM Model Provider interface and error definitions.

Exposes the abstract ModelProvider (LLMProvider) contract and exception hierarchy.
Adheres to:
- PHASE4_IMPLEMENTATIONPLAN.md §5 (Iteration 3)
- Decision 033: Decoupled Model Provider Protocol & LM Studio (Bionic) Compatibility
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

__all__ = [
    "LLMProvider",
    "ModelProvider",
    "ModelProviderError",
    "ModelConnectionError",
    "ModelAPIError",
    "ModelTimeoutError",
    "ModelResponseError",
]
