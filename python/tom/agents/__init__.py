"""TOM agents subsystem.

Exposes foundational Agent abstraction, dependencies container, lifecycle state machine,
message models, and conversation context.
"""

from tom.agents.base import (
    VALID_TRANSITIONS,
    Agent,
    AgentAwareConfirmationHook,
)
from tom.agents.dependencies import AgentDependencies
from tom.schemas.agent import (
    AgentConfig,
    AgentIdentity,
    AgentState,
    ConversationHistory,
    InvalidStateTransitionError,
    Message,
    Role,
    StateChangeEvent,
    ToolCallMetadata,
)

__all__ = [
    "VALID_TRANSITIONS",
    "Agent",
    "AgentAwareConfirmationHook",
    "AgentConfig",
    "AgentDependencies",
    "AgentIdentity",
    "AgentState",
    "ConversationHistory",
    "InvalidStateTransitionError",
    "Message",
    "Role",
    "StateChangeEvent",
    "ToolCallMetadata",
]
