"""Pydantic schemas and models for TOM Agents.

Adheres to:
- PHASE4_IMPLEMENTATIONPLAN.md §5 (Iteration 1)
- Decision 031: Agent State Machine Architecture & Invariants
- skills/coding/validation: Strict bounds, timezone-aware datetimes, Pydantic v2 validation.
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class AgentState(StrEnum):
    """Deterministic 6-state lifecycle for TOM Agents."""

    IDLE = "IDLE"
    THINKING = "THINKING"
    ACTING = "ACTING"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    ERROR = "ERROR"
    TERMINATED = "TERMINATED"


class Role(StrEnum):
    """Role classification for conversational messages."""

    SYSTEM = "SYSTEM"
    USER = "USER"
    ASSISTANT = "ASSISTANT"
    TOOL_CALL = "TOOL_CALL"
    TOOL_RESULT = "TOOL_RESULT"


class AgentIdentity(BaseModel):
    """Unique, stable identity for an Agent instance."""

    agent_id: str = Field(
        default_factory=lambda: f"agent_{uuid.uuid4().hex[:12]}",
        description="Unique identifier for the agent instance",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timezone-aware creation timestamp (UTC)",
    )


class AgentConfig(BaseModel):
    """Configuration parameters for an Agent instance."""

    name: str = Field(
        default="tom-agent",
        description="Human-readable name of the agent",
    )
    max_steps: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum reasoning/acting steps before forced termination",
    )
    system_prompt: str = Field(
        default="You are TOM, a helpful personal AI assistant.",
        description="System prompt defining agent persona and boundary",
    )
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Sampling temperature for the reasoning model",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Agent name cannot be empty or whitespace only")
        return trimmed


class ToolCallMetadata(BaseModel):
    """Metadata associated with tool invocations or results."""

    tool_name: str | None = None
    tool_call_id: str | None = None
    parameters: dict[str, Any] | None = None
    execution_time_ms: float | None = None
    success: bool | None = None
    error: str | None = None


class Message(BaseModel):
    """Typed conversational message model."""

    role: Role = Field(..., description="Role of the message author")
    content: str = Field(..., description="Text content of the message")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timezone-aware creation timestamp (UTC)",
    )
    name: str | None = Field(
        default=None,
        description="Optional sender or tool name",
    )
    tool_call_id: str | None = Field(
        default=None,
        description="Correlation ID for tool call / tool result pairing",
    )
    tool_metadata: ToolCallMetadata | dict[str, Any] | None = Field(
        default=None,
        description="Tool execution metadata where applicable",
    )

    @field_validator("timestamp")
    @classmethod
    def ensure_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v


class ConversationHistory(BaseModel):
    """Bounded, ordered message container with trimming and serialization."""

    messages: list[Message] = Field(
        default_factory=list,
        description="Ordered list of conversation messages",
    )
    max_messages: int | None = Field(
        default=100,
        gt=0,
        description="Upper bound on stored messages; older messages trimmed when exceeded",
    )

    def append(self, message: Message | dict[str, Any]) -> None:
        """Append a message to history, validating and enforcing bounds."""
        if isinstance(message, dict):
            validated_msg = Message.model_validate(message)
        elif isinstance(message, Message):
            validated_msg = message
        else:
            raise TypeError(f"Expected Message or dict, got {type(message).__name__}")

        self.messages.append(validated_msg)
        if self.max_messages is not None and len(self.messages) > self.max_messages:
            self.trim(self.max_messages)

    def trim(self, max_count: int) -> int:
        """Trim history to max_count messages, preserving system prompt if present.

        Returns the count of removed messages.
        """
        if max_count <= 0:
            removed = len(self.messages)
            self.messages.clear()
            return removed

        if len(self.messages) <= max_count:
            return 0

        excess = len(self.messages) - max_count

        # Check if first message is a SYSTEM message
        if self.messages and self.messages[0].role == Role.SYSTEM:
            if max_count == 1:
                removed = len(self.messages) - 1
                self.messages = [self.messages[0]]
                return removed
            # Preserve system message, trim oldest non-system messages
            system_msg = self.messages[0]
            non_system = self.messages[1:]
            trimmed_non_system = non_system[excess:]
            removed = len(non_system) - len(trimmed_non_system)
            self.messages = [system_msg] + trimmed_non_system
            return removed
        else:
            self.messages = self.messages[excess:]
            return excess

    def clear(self, keep_system: bool = True) -> None:
        """Clear messages, optionally preserving the initial SYSTEM message."""
        if keep_system and self.messages and self.messages[0].role == Role.SYSTEM:
            self.messages = [self.messages[0]]
        else:
            self.messages.clear()

    def get_messages(self) -> list[Message]:
        """Return a copy of messages list."""
        return list(self.messages)

    def __len__(self) -> int:
        return len(self.messages)

    def __iter__(self):
        return iter(self.messages)

    def __getitem__(self, index: int | slice):
        return self.messages[index]


class StateChangeEvent(BaseModel):
    """Event emitted when an Agent transitions from one state to another."""

    agent_id: str = Field(..., description="ID of the transitioning agent")
    old_state: AgentState = Field(..., description="Previous agent state")
    new_state: AgentState = Field(..., description="New agent state")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timezone-aware timestamp of transition",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Contextual metadata for the transition",
    )


class InvalidStateTransitionError(Exception):
    """Raised when an Agent attempts an illegal state transition."""

    def __init__(
        self,
        current_state: AgentState,
        target_state: AgentState,
        message: str | None = None,
    ) -> None:
        self.current_state = current_state
        self.target_state = target_state
        msg = (
            message
            or f"Invalid state transition: cannot transition from {current_state.value} to {target_state.value}"
        )
        super().__init__(msg)
