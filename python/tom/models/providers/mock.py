"""MockModelProvider — Deterministic, configurable test double for LLMProvider.

Adheres to:
- PHASE4_IMPLEMENTATIONPLAN.md §5 (Iteration 3)
- skills/testing/python-testing: Deterministic, injectable, no network deps

Use MockModelProvider in unit tests to:
- Program scripted responses without network access
- Inject scripted tool-call sequences
- Simulate latency, errors, and cancellation

Streaming interface contract:
    The stream() method is an async generator. Callers iterate with:
        async for chunk in provider.stream(request): ...
    No `await` is used on stream() itself — it returns an AsyncIterator directly.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from tom.models.base import LLMProvider, ModelResponseError
from tom.schemas.models import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    StreamChunk,
    TokenUsage,
    ToolCallRequest,
)
from tom.telemetry.logging import get_logger

logger = get_logger(__name__)


class MockModelProvider(LLMProvider):
    """Fully deterministic test double for the LLMProvider protocol.

    Responses are consumed from a scripted queue. When the queue is
    exhausted the mock raises ``ModelResponseError`` unless ``loop`` is True.

    Args:
        responses: Ordered list of scripted responses to return sequentially.
            Each entry may be:
            - str: Returned as ``content`` in a successful ModelResponse.
            - ModelResponse: Returned directly.
            - Exception subclass instance: Raised when this entry is reached.
        loop: If True, responses cycle indefinitely (default: False).
        latency_seconds: Optional artificial delay per call (default: 0.0).
        health: Value returned by ``check_health()`` (default: True).
    """

    def __init__(
        self,
        responses: list[str | ModelResponse | BaseException] | None = None,
        *,
        loop: bool = False,
        latency_seconds: float = 0.0,
        health: bool = True,
    ) -> None:
        self._responses: list[str | ModelResponse | BaseException] = responses or []
        self._loop = loop
        self._latency_seconds = latency_seconds
        self._health = health
        self._call_count = 0
        self._index = 0

    @property
    def call_count(self) -> int:
        """Total number of generate/stream calls made."""
        return self._call_count

    def _next_entry(self) -> str | ModelResponse | BaseException:
        """Pop the next scripted entry from the queue."""
        if not self._responses:
            raise ModelResponseError("MockModelProvider: response queue is empty")
        if self._loop:
            entry = self._responses[self._index % len(self._responses)]
            self._index += 1
        else:
            if self._index >= len(self._responses):
                raise ModelResponseError(
                    f"MockModelProvider: response queue exhausted after {self._call_count} calls"
                )
            entry = self._responses[self._index]
            self._index += 1
        return entry

    def _build_response(
        self,
        entry: str | ModelResponse | BaseException,
        request: ModelRequest,
    ) -> ModelResponse:
        """Convert a scripted entry into a ModelResponse (or raise)."""
        if isinstance(entry, BaseException):
            raise entry
        if isinstance(entry, ModelResponse):
            return entry
        # Plain string → text response
        return ModelResponse(
            content=str(entry),
            finish_reason=FinishReason.STOP,
            model_id=request.model,
            usage=TokenUsage(
                prompt_tokens=len(" ".join(m.content for m in request.messages).split()),
                completion_tokens=len(str(entry).split()),
            ),
        )

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Return the next scripted response.

        Args:
            request: The model request (used for building fallback responses).

        Returns:
            ModelResponse from the scripted queue.

        Raises:
            Any exception instance placed in the scripted queue.
            ModelResponseError: Queue exhausted.
            asyncio.CancelledError: Caller cancelled task (re-raised cleanly).
        """
        self._call_count += 1
        if self._latency_seconds > 0:
            await asyncio.sleep(self._latency_seconds)
        entry = self._next_entry()
        response = self._build_response(entry, request)
        logger.info(
            "mock_provider_generate",
            model=request.model,
            call_count=self._call_count,
            content_preview=response.content[:40],
        )
        return response

    async def stream(  # type: ignore[override]
        self,
        request: ModelRequest,
    ) -> AsyncIterator[StreamChunk]:
        """Yield scripted text as incremental word-level StreamChunk objects.

        This is an async generator. Callers use::

            async for chunk in provider.stream(request):
                ...

        If the scripted entry is an Exception, it is raised immediately
        (before any chunks are yielded).

        Args:
            request: The model request.

        Yields:
            StreamChunk objects (content words + final sentinel with is_final=True).

        Raises:
            Any exception instance placed in the scripted queue.
            ModelResponseError: Queue exhausted.
            asyncio.CancelledError: Caller cancelled task (re-raised cleanly).
        """
        self._call_count += 1
        if self._latency_seconds > 0:
            await asyncio.sleep(self._latency_seconds)
        entry = self._next_entry()

        # Raise errors before emitting any chunks
        if isinstance(entry, BaseException):
            raise entry

        response = self._build_response(entry, request)
        words = response.content.split() if response.content else [""]

        for i, word in enumerate(words):
            text = word + (" " if i < len(words) - 1 else "")
            if self._latency_seconds > 0:
                await asyncio.sleep(self._latency_seconds / max(len(words), 1))
            yield StreamChunk(content=text)

        # Final sentinel chunk
        yield StreamChunk(
            content="",
            finish_reason=response.finish_reason,
            usage=response.usage,
            is_final=True,
        )

    async def check_health(self) -> bool:
        """Return the configured health value."""
        return self._health

    def reset(self) -> None:
        """Reset call count and queue index to the beginning."""
        self._call_count = 0
        self._index = 0

    def add_response(self, response: str | ModelResponse | BaseException) -> None:
        """Append a response to the scripted queue."""
        self._responses.append(response)

    def add_tool_call_response(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tool_call_id: str = "tc_mock_001",
    ) -> None:
        """Append a scripted tool-call response to the queue."""
        self._responses.append(
            ModelResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id=tool_call_id,
                        name=tool_name,
                        arguments=arguments,
                    )
                ],
                finish_reason=FinishReason.TOOL_CALL,
            )
        )
