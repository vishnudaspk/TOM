"""Foundational Agent abstraction, deterministic state machine, and tool execution.

Adheres to:
- PHASE4_IMPLEMENTATIONPLAN.md §5 (Iteration 1 & 2)
- Decision 031: Agent State Machine Architecture & Invariants
- Decision 032: Centralized Tool Invocation Safety (Strict ToolExecutor Routing)
- 6-state explicit lifecycle: IDLE, THINKING, ACTING, WAITING_CONFIRMATION, ERROR, TERMINATED.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

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
from tom.security.confirmation import ConfirmationHook, ConfirmationRequest
from tom.security.permissions import PermissionDeniedError
from tom.telemetry.logging import get_logger
from tom.tools.registry import ToolNotFoundError, ToolResult, ToolValidationError

if TYPE_CHECKING:
    from tom.agents.dependencies import AgentDependencies

logger = get_logger(__name__)

# Authoritative State Transition Matrix (Decision 031)
VALID_TRANSITIONS: dict[AgentState, set[AgentState]] = {
    AgentState.IDLE: {
        AgentState.THINKING,
        AgentState.TERMINATED,
    },
    AgentState.THINKING: {
        AgentState.ACTING,
        AgentState.WAITING_CONFIRMATION,
        AgentState.IDLE,
        AgentState.ERROR,
        AgentState.TERMINATED,
    },
    AgentState.ACTING: {
        AgentState.THINKING,
        AgentState.WAITING_CONFIRMATION,
        AgentState.IDLE,
        AgentState.ERROR,
        AgentState.TERMINATED,
    },
    AgentState.WAITING_CONFIRMATION: {
        AgentState.ACTING,
        AgentState.IDLE,
        AgentState.ERROR,
        AgentState.TERMINATED,
    },
    AgentState.ERROR: {
        AgentState.IDLE,
        AgentState.TERMINATED,
    },
    AgentState.TERMINATED: set(),
}


class AgentAwareConfirmationHook(ConfirmationHook):
    """Coordinates Agent state during interactive ASK_USER confirmation flows.

    Transitions the Agent to WAITING_CONFIRMATION while waiting on the underlying hook,
    and returns to ACTING upon approval.
    """

    def __init__(self, inner_hook: ConfirmationHook, agent: Agent) -> None:
        self.inner_hook = inner_hook
        self.agent = agent

    async def request_confirmation(self, request: ConfirmationRequest) -> bool:
        """Forward confirmation request while synchronizing Agent lifecycle state."""
        if self.agent.state == AgentState.ACTING:
            self.agent.transition_to(
                AgentState.WAITING_CONFIRMATION,
                metadata={
                    "tool_name": request.tool_name,
                    "description": request.description,
                    "params": request.params,
                },
            )

        try:
            confirmed = await self.inner_hook.request_confirmation(request)
        except Exception as e:
            logger.warning(
                "agent_confirmation_hook_exception",
                agent_id=self.agent.agent_id,
                tool_name=request.tool_name,
                error=str(e),
            )
            confirmed = False

        if confirmed and self.agent.state == AgentState.WAITING_CONFIRMATION:
            self.agent.transition_to(
                AgentState.ACTING,
                metadata={"confirmed": True, "tool_name": request.tool_name},
            )
        return confirmed


class Agent:
    """Base Agent abstraction governing lifecycle state, conversation context, and safe tool dispatch.

    Owns an explicit 6-state lifecycle with strict transition validation and delegates all
    tool execution exclusively to Phase 3's ToolExecutor (Decision 032).
    """

    def __init__(
        self,
        config: AgentConfig | None = None,
        identity: AgentIdentity | None = None,
        history: ConversationHistory | None = None,
        dependencies: AgentDependencies | None = None,
    ) -> None:
        self._identity = identity or AgentIdentity()
        self._config = config or AgentConfig()
        self._state = AgentState.IDLE
        self._callbacks: list[Callable[..., Any]] = []
        self._dependencies = dependencies

        if history is not None:
            self._history = history
        else:
            self._history = ConversationHistory()
            if self._config.system_prompt:
                self._history.append(
                    Message(
                        role=Role.SYSTEM,
                        content=self._config.system_prompt,
                    )
                )

    @property
    def identity(self) -> AgentIdentity:
        """Return the unique identity of this agent."""
        return self._identity

    @property
    def agent_id(self) -> str:
        """Return the agent's unique string identifier."""
        return self._identity.agent_id

    @property
    def config(self) -> AgentConfig:
        """Return the configuration for this agent."""
        return self._config

    @property
    def history(self) -> ConversationHistory:
        """Return the conversation history container."""
        return self._history

    @property
    def state(self) -> AgentState:
        """Return the current deterministic lifecycle state."""
        return self._state

    @property
    def dependencies(self) -> AgentDependencies:
        """Return the agent's dependencies container, lazily creating defaults if None."""
        if self._dependencies is None:
            from tom.agents.dependencies import AgentDependencies

            self._dependencies = AgentDependencies()
        return self._dependencies

    @property
    def model_provider(self) -> Any:
        """Return the model provider configured in dependencies, or None."""
        return self.dependencies.model_provider

    def add_state_callback(self, callback: Callable[..., Any]) -> None:
        """Register a callback invoked whenever the agent transitions state.

        Callback may accept either (event: StateChangeEvent) or (old_state, new_state).
        """
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def remove_state_callback(self, callback: Callable[..., Any]) -> None:
        """Unregister a state change callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def transition_to(
        self,
        target_state: AgentState,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Perform a validated state transition.

        Args:
            target_state: Target AgentState to transition to.
            metadata: Optional contextual metadata describing the transition.

        Raises:
            InvalidStateTransitionError: If the transition is not permitted by VALID_TRANSITIONS.
        """
        valid_targets = VALID_TRANSITIONS.get(self._state, set())
        if target_state not in valid_targets:
            raise InvalidStateTransitionError(
                current_state=self._state,
                target_state=target_state,
            )

        old_state = self._state
        self._state = target_state

        event = StateChangeEvent(
            agent_id=self._identity.agent_id,
            old_state=old_state,
            new_state=target_state,
            metadata=metadata or {},
        )

        for callback in list(self._callbacks):
            try:
                try:
                    callback(event)
                except TypeError:
                    callback(old_state, target_state)
            except Exception as e:
                logger.error(
                    "agent_state_callback_failed",
                    agent_id=self._identity.agent_id,
                    old_state=old_state.value,
                    new_state=target_state.value,
                    error=str(e),
                )

    async def execute_tool(
        self,
        tool_name: str,
        params: dict[str, Any] | BaseModel | None = None,
        timeout_seconds: float | None = None,
        return_to_state: AgentState = AgentState.THINKING,
        tool_call_id: str | None = None,
    ) -> ToolResult:
        """Execute a tool via the authoritative ToolExecutor pipeline with strict safety.

        Adheres to Decision 032: Centralized Tool Invocation Safety.
        Never calls tool handlers directly, never evaluates permissions independently.

        Transitions:
        - If IDLE: transitions to THINKING, then ACTING.
        - If THINKING: transitions to ACTING.
        - If WAITING_CONFIRMATION: coordinated via AgentAwareConfirmationHook.
        - On completion / error: transitions back to return_to_state.
        - Results or errors are recorded in ConversationHistory as Role.TOOL_RESULT messages.
        """
        # Ensure agent enters ACTING state
        if self._state == AgentState.IDLE:
            self.transition_to(AgentState.THINKING, metadata={"tool_name": tool_name})
            self.transition_to(AgentState.ACTING, metadata={"tool_name": tool_name})
        elif self._state == AgentState.THINKING:
            self.transition_to(AgentState.ACTING, metadata={"tool_name": tool_name})
        elif self._state != AgentState.ACTING:
            self.transition_to(AgentState.ACTING, metadata={"tool_name": tool_name})

        executor = self.dependencies.get_executor()
        original_hook = executor.confirmation_hook

        # Wrap confirmation hook to reflect WAITING_CONFIRMATION in agent lifecycle
        if (
            not isinstance(original_hook, AgentAwareConfirmationHook)
            or original_hook.agent is not self
        ):
            executor.confirmation_hook = AgentAwareConfirmationHook(original_hook, self)

        try:
            result = await executor.execute(
                tool_name=tool_name,
                params=params,
                timeout_seconds=timeout_seconds,
            )
        except asyncio.CancelledError:
            logger.info(
                "agent_tool_execution_cancelled",
                agent_id=self.agent_id,
                tool_name=tool_name,
            )
            if (
                self._state in VALID_TRANSITIONS
                and AgentState.TERMINATED in VALID_TRANSITIONS[self._state]
            ):
                self.transition_to(AgentState.TERMINATED, metadata={"reason": "cancelled"})
            raise
        except PermissionDeniedError as e:
            logger.warning(
                "agent_tool_permission_denied",
                agent_id=self.agent_id,
                tool_name=tool_name,
                error=str(e),
            )
            result = ToolResult.fail(error=str(e))
        except (ToolNotFoundError, ToolValidationError) as e:
            logger.warning(
                "agent_tool_validation_error",
                agent_id=self.agent_id,
                tool_name=tool_name,
                error=str(e),
            )
            result = ToolResult.fail(error=str(e))
        except Exception as e:
            logger.error(
                "agent_tool_unexpected_error",
                agent_id=self.agent_id,
                tool_name=tool_name,
                error=str(e),
            )
            result = ToolResult.fail(error=f"Unexpected error executing '{tool_name}': {e}")
        finally:
            executor.confirmation_hook = original_hook

        # Record observation in ConversationHistory
        if result.success:
            if isinstance(result.data, BaseModel):
                content_str = result.data.model_dump_json()
            elif isinstance(result.data, (dict, list)):
                try:
                    content_str = json.dumps(result.data)
                except Exception:
                    content_str = str(result.data)
            else:
                content_str = (
                    str(result.data) if result.data is not None else "Tool executed successfully"
                )
        else:
            content_str = result.error or "Tool execution failed"

        obs_msg = Message(
            role=Role.TOOL_RESULT,
            content=content_str,
            name=tool_name,
            tool_call_id=tool_call_id,
            tool_metadata=ToolCallMetadata(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                execution_time_ms=result.execution_time_ms,
                success=result.success,
                error=result.error,
            ),
        )
        self._history.append(obs_msg)

        # Transition safely back to desired state
        if self._state == AgentState.WAITING_CONFIRMATION:
            self.transition_to(
                AgentState.IDLE,
                metadata={"reason": "confirmation_denied"},
            )
            if return_to_state == AgentState.THINKING:
                self.transition_to(AgentState.THINKING)
        elif self._state == AgentState.ACTING:
            self.transition_to(return_to_state, metadata={"tool_name": tool_name})

        return result
