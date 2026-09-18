"""Abstract LLMProvider protocol — TOM's model inference abstraction boundary.

Adheres to:
- PHASE4_IMPLEMENTATIONPLAN.md §5 (Iteration 3)
- Decision 033: Decoupled Model Provider Protocol & LM Studio (Bionic) Compatibility

The Agent depends on this abstract protocol — never on LMStudioProvider directly.
Concrete providers implement generate() and stream() and optionally check_health().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tom.schemas.models import ModelRequest, ModelResponse, StreamChunk


class ModelProviderError(Exception):
    """Base class for model provider errors.

    Never surfaces raw HTTP library exceptions through the public provider interface.
    """


class ModelConnectionError(ModelProviderError):
    """Raised when the provider endpoint cannot be reached."""


class ModelAPIError(ModelProviderError):
    """Raised when the provider returns an HTTP/API-level failure (4xx/5xx)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ModelTimeoutError(ModelProviderError):
    """Raised when a generate/stream request exceeds the configured timeout."""


class ModelResponseError(ModelProviderError):
    """Raised when the provider returns a malformed or unexpected response."""


class LLMProvider(ABC):
    """Abstract base class for all TOM language model providers.

    All providers must implement this interface. The Agent depends only on
    LLMProvider — never on concrete implementations (Decision 033).

    Protocol:
        async generate(request) -> ModelResponse
            Perform a non-streaming completion; return a fully assembled response.

        async stream(request) -> AsyncIterator[StreamChunk]
            Perform a streaming completion; yield incremental chunks.
            Callers accumulate content across chunks; the final chunk has is_final=True.

        async check_health() -> bool
            Return True if the provider endpoint is reachable and serving requests.
            Should not raise; return False on any failure.

    Error convention:
        - Connection failures      → ModelConnectionError
        - HTTP/API failures        → ModelAPIError (with status_code)
        - Timeout                  → ModelTimeoutError
        - Malformed response       → ModelResponseError
        - asyncio.CancelledError   → re-raised, never swallowed
        - Programming errors       → allowed to propagate naturally
    """

    @abstractmethod
    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Perform a non-streaming model completion.

        Args:
            request: Typed TOM model request.

        Returns:
            Fully assembled ModelResponse.

        Raises:
            ModelConnectionError: Endpoint not reachable.
            ModelAPIError: Provider returned HTTP error.
            ModelTimeoutError: Request exceeded timeout.
            ModelResponseError: Response payload is malformed.
            asyncio.CancelledError: Caller cancelled the task (re-raised).
        """
        ...

    @abstractmethod
    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        """Perform a streaming model completion.

        Yields incremental StreamChunk objects. The final chunk has is_final=True
        and may contain finish_reason and usage metadata.

        Args:
            request: Typed TOM model request (stream field is forced True internally).

        Yields:
            StreamChunk objects with incremental content.

        Raises:
            ModelConnectionError: Endpoint not reachable.
            ModelAPIError: Provider returned HTTP error.
            ModelTimeoutError: Initial connection exceeded timeout.
            ModelResponseError: SSE/chunk payload is malformed.
            asyncio.CancelledError: Caller cancelled the task (re-raised).
        """
        ...

    @abstractmethod
    async def check_health(self) -> bool:
        """Return True if the provider is reachable and serving requests.

        Must not raise; return False on any failure.
        """
        ...


# ModelProvider is an authoritative alias for LLMProvider
ModelProvider = LLMProvider
