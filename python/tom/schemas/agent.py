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

from tom.identity import ASSISTANT_NAME


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
        default=f"You are {ASSISTANT_NAME}, a helpful personal AI assistant.",
        description="System prompt defining agent persona and boundary",
    )
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Sampling temperature for the reasoning model",
    )
    reasoning_vram_mb: int = Field(
        default=5200,
        ge=0,
        le=8192,
        description="VRAM requirement for the reasoning model in MB",
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
    tool_calls: list[Any] | None = Field(
        default=None,
        description="Tool calls emitted by the assistant in this message",
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
    max_tokens: int | None = Field(
        default=2048,
        gt=0,
        description="Upper bound on estimated tokens in history; older messages trimmed when exceeded",
    )

    @staticmethod
    def estimate_message_tokens(msg: Message | None) -> int:
        """Estimate tokens for a single message (~4 chars/token + 4 overhead)."""
        if msg is None:
            return 0
        content_len = len(msg.content or "")
        meta_len = len(str(msg.tool_metadata)) if msg.tool_metadata else 0
        return 4 + max(1, (content_len + meta_len) // 4 + 1)

    def total_tokens(self) -> int:
        """Estimate total tokens in conversation history."""
        return sum(self.estimate_message_tokens(m) for m in self.messages)

    def append(self, message: Message | dict[str, Any]) -> None:
        """Append a message to history, validating and enforcing count and token bounds."""
        if isinstance(message, dict):
            validated_msg = Message.model_validate(message)
        elif isinstance(message, Message):
            validated_msg = message
        else:
            raise TypeError(f"Expected Message or dict, got {type(message).__name__}")

        self.messages.append(validated_msg)
        if (self.max_messages is not None and len(self.messages) > self.max_messages) or (
            self.max_tokens is not None and self.total_tokens() > self.max_tokens
        ):
            self.trim()

    def trim(
        self,
        max_count: int | None = None,
        max_tokens: int | None = None,
    ) -> int:
        """Trim history preserving system prompt, tool relationships, and token bounds.

        Returns the count of removed messages.
        """
        limit_count = max_count if max_count is not None else self.max_messages
        limit_tokens = max_tokens if max_tokens is not None else self.max_tokens

        if not self.messages:
            return 0

        initial_len = len(self.messages)
        if limit_count is not None and limit_count <= 0:
            self.messages.clear()
            return initial_len

        system_msg: Message | None = None
        work_messages = list(self.messages)

        if work_messages and work_messages[0].role == Role.SYSTEM:
            system_msg = work_messages.pop(0)

        # 1. Enforce message count limit
        if limit_count is not None:
            target_non_system = limit_count - (1 if system_msg else 0)
            if target_non_system <= 0:
                self.messages = [system_msg] if system_msg else []
                return initial_len - len(self.messages)
            if len(work_messages) > target_non_system:
                work_messages = work_messages[-target_non_system:]

        # 2. Enforce token budget limit
        if limit_tokens is not None and limit_tokens > 0:
            sys_tokens = self.estimate_message_tokens(system_msg)
            available_tokens = max(0, limit_tokens - sys_tokens)

            # Walk backwards from newest to oldest to keep what fits in budget
            accumulated: list[Message] = []
            used_tokens = 0
            for msg in reversed(work_messages):
                t = self.estimate_message_tokens(msg)
                if used_tokens + t > available_tokens and accumulated:
                    break
                accumulated.append(msg)
                used_tokens += t
            work_messages = list(reversed(accumulated))

        # 3. Preserve tool relationship integrity (never orphan tool results)
        while work_messages and work_messages[0].role == Role.TOOL_RESULT:
            work_messages.pop(0)

        self.messages = ([system_msg] if system_msg else []) + work_messages
        return initial_len - len(self.messages)

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
