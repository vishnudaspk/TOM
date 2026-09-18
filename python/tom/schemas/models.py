"""Pydantic schemas for TOM Model Provider boundary.

Adheres to:
- PHASE4_IMPLEMENTATIONPLAN.md §5 (Iteration 3)
- Decision 033: Decoupled Model Provider Protocol & LM Studio (Bionic) Compatibility
- skills/coding/validation: Strict bounds, timezone-aware datetimes, Pydantic v2 validation.

These schemas represent TOM's abstraction boundary — they are NOT OpenAI-specific.
The provider layer translates between these schemas and whatever wire format each
concrete provider requires.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Re-export Role/Message for convenience; providers consume existing schemas.
from tom.schemas.agent import Message, Role

__all__ = [
    "FinishReason",
    "Message",
    "ModelRequest",
    "ModelResponse",
    "Role",
    "StreamChunk",
    "TokenUsage",
    "ToolCallRequest",
]


class FinishReason(StrEnum):
    """Reason a model generation ended."""

    STOP = "stop"
    LENGTH = "length"
    TOOL_CALL = "tool_call"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"
    UNKNOWN = "unknown"


class TokenUsage(BaseModel):
    """Token consumption metadata returned by the provider."""

    model_config = ConfigDict(extra="ignore")

    prompt_tokens: int = Field(ge=0, default=0)
    completion_tokens: int = Field(ge=0, default=0)
    total_tokens: int = Field(ge=0, default=0)
    reasoning_tokens: int = Field(ge=0, default=0, description="Thinking/chain-of-thought tokens")


class ToolCallRequest(BaseModel):
    """A structured tool-call request emitted by the model.

    The model produces this; it does NOT execute the tool — the Agent/Executor does.
    """

    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="Provider-assigned tool call correlation ID")
    name: str = Field(description="Tool function name (e.g. 'system.cpu_info')")
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON-decoded tool arguments",
    )


class ModelRequest(BaseModel):
    """Provider-agnostic request sent to a language model.

    This is TOM's abstraction — not tied to any specific API wire format.
    The concrete provider maps this to whatever the endpoint requires.
    """

    model_config = ConfigDict(extra="forbid")

    model: str = Field(description="Model identifier (e.g. 'qwen3-8b')")
    messages: list[Message] = Field(
        min_length=1,
        description="Ordered conversation history to send to the model",
    )
    max_tokens: int = Field(
        default=1024,
        ge=1,
        le=32768,
        description="Maximum tokens to generate",
    )
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Sampling temperature",
    )
    stream: bool = Field(
        default=False,
        description="Whether to request a streaming response",
    )
    tools: list[dict[str, Any]] | None = Field(
        default=None,
        description="Optional JSON tool schemas (OpenAI-compatible format)",
    )
    extra_params: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific extra parameters passed through opaquely",
    )


class ModelResponse(BaseModel):
    """Provider-agnostic response from a language model.

    This is TOM's abstraction — the concrete provider maps its wire response here.
    """

    model_config = ConfigDict(extra="ignore")

    content: str = Field(default="", description="Generated text content")
    reasoning_content: str = Field(
        default="",
        description="Chain-of-thought / thinking tokens (Qwen3, DeepSeek, etc.)",
    )
    tool_calls: list[ToolCallRequest] = Field(
        default_factory=list,
        description="Structured tool-call requests emitted by the model",
    )
    finish_reason: FinishReason = Field(
        default=FinishReason.UNKNOWN,
        description="Why the generation stopped",
    )
    usage: TokenUsage = Field(
        default_factory=TokenUsage,
        description="Token consumption metadata",
    )
    model_id: str = Field(
        default="",
        description="Model identifier as reported by the provider",
    )
    provider_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque provider-specific metadata for debugging/logging",
    )

    @property
    def has_tool_calls(self) -> bool:
        """Return True if the model produced tool-call requests."""
        return len(self.tool_calls) > 0

    @property
    def is_complete(self) -> bool:
        """Return True if the model finished generating (not interrupted)."""
        return self.finish_reason in {FinishReason.STOP, FinishReason.TOOL_CALL}


class StreamChunk(BaseModel):
    """A single incremental chunk from a streaming model response.

    Accumulate ``content`` fields across all chunks to assemble the full response.
    The final chunk where ``is_final`` is True carries ``finish_reason`` and ``usage``.
    """

    model_config = ConfigDict(extra="ignore")

    content: str = Field(default="", description="Incremental text delta for this chunk")
    reasoning_content: str = Field(
        default="",
        description="Incremental thinking/chain-of-thought delta",
    )
    tool_call_delta: ToolCallRequest | None = Field(
        default=None,
        description="Partial tool-call delta (if the model is streaming a tool call)",
    )
    finish_reason: FinishReason | None = Field(
        default=None,
        description="Set only on the final chunk; None for intermediate chunks",
    )
    usage: TokenUsage | None = Field(
        default=None,
        description="Token usage reported on the final chunk (if available)",
    )
    is_final: bool = Field(
        default=False,
        description="True when this is the last chunk in the stream",
    )
