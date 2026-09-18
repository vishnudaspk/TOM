"""Multi-step Agent Orchestrator with bounded reasoning and cancellation.

Adheres to:
- PHASE4_IMPLEMENTATIONPLAN.md §5 (Iteration 5)
- Decision 035: Step-Bounded Execution Loop & Cooperative Task Cancellation

Orchestrates the complete pipeline:
    User Prompt → IntentRouter → Direct Tool or Agent Loop → Response
"""

from __future__ import annotations

import asyncio
import json

from pydantic import BaseModel

from tom.agents.base import Agent, AgentState
from tom.core.context import CancellationToken
from tom.core.router import IntentRouter
from tom.models.base import LLMProvider
from tom.models.schemas import ModelRequest, ModelResponse
from tom.schemas.agent import Message, Role
from tom.schemas.router import RoutingDecision
from tom.telemetry.logging import get_logger
from tom.tools.registry import ToolResult

logger = get_logger(__name__)

_DEFAULT_MAX_STEPS = 10


class AgentOrchestrator:
    """Bounded multi-step reasoning and tool execution orchestrator.

    Manages the complete agent workflow:
    1. Route user request via IntentRouter
    2. Direct tool execution for deterministic Tier 1 matches
    3. Multi-step model → tool → observation loop for complex requests

    The orchestrator does not own agent state transitions — it delegates to
    the Agent instance. It enforces step bounds and cooperative cancellation.
    """

    def __init__(
        self,
        agent: Agent,
        router: IntentRouter,
    ) -> None:
        self._agent = agent
        self._router = router

    @property
    def agent(self) -> Agent:
        """Return the managed Agent instance."""
        return self._agent

    async def run(
        self,
        prompt: str,
        cancellation_token: CancellationToken | None = None,
        max_steps: int = _DEFAULT_MAX_STEPS,
    ) -> str:
        """Run the orchestration loop for a user prompt.

        Args:
            prompt: User request text.
            cancellation_token: Optional cooperative cancellation token.
            max_steps: Maximum reasoning/acting steps before forced termination.

        Returns:
            Final response text from the agent.
        """
        # Check initial cancellation
        if self._check_cancelled(cancellation_token):
            return "Task cancelled"

        # Route the request
        route: RoutingDecision = await self._router.classify(prompt)

        # Add user message to history
        self._agent.history.append(Message(role=Role.USER, content=prompt))

        # Tier 1: direct tool execution
        if route.target_tool is not None:
            return await self._execute_direct_tool(route.target_tool)

        # No Tier 1 match: check if we have a provider for multi-step
        if self._agent.model_provider is None:
            if self._agent.state != AgentState.TERMINATED:
                self._agent.transition_to(AgentState.THINKING)
                self._agent.transition_to(AgentState.ERROR)
            return "I'm not sure how to help with that"

        # Tier 2: multi-step agent loop
        return await self._run_agent_loop(
            prompt,
            cancellation_token,
            max_steps,
        )

    async def _execute_direct_tool(self, tool_name: str) -> str:
        """Execute a single tool and return its result text."""
        if self._agent.state == AgentState.IDLE:
            self._agent.transition_to(AgentState.THINKING)

        result: ToolResult = await self._agent.execute_tool(tool_name=tool_name)

        if self._agent.state == AgentState.THINKING:
            self._agent.transition_to(AgentState.IDLE)

        if result.success:
            data = result.data
            if isinstance(data, BaseModel):
                return data.model_dump_json()
            if isinstance(data, (dict, list)):
                try:
                    return json.dumps(data)
                except Exception:
                    return str(data)
            return str(data) if data is not None else "Tool executed successfully"
        return result.error or "Tool execution failed"

    async def _run_agent_loop(
        self,
        prompt: str,
        cancellation_token: CancellationToken | None,
        max_steps: int,
    ) -> str:
        """Run the bounded multi-step model → tool → observation loop."""

        for step in range(max_steps):
            # Check cancellation before each step
            if self._check_cancelled(cancellation_token):
                return "Task cancelled"

            # Transition to THINKING
            if (
                self._agent.state != AgentState.TERMINATED
                and self._agent.state != AgentState.THINKING
            ):
                self._agent.transition_to(
                    AgentState.THINKING,
                    metadata={"step": step + 1},
                )

            try:
                response = await self._think()
            except asyncio.CancelledError:
                if self._agent.state != AgentState.TERMINATED:
                    self._agent.transition_to(
                        AgentState.TERMINATED,
                        metadata={"reason": "cancelled"},
                    )
                raise
            except Exception as exc:
                logger.error(
                    "orchestrator_model_error",
                    step=step + 1,
                    error=str(exc),
                )
                if self._agent.state == AgentState.THINKING:
                    self._agent.transition_to(AgentState.ERROR)
                return f"Model error: {exc}"

            # Record model response in history
            self._agent.history.append(Message(role=Role.ASSISTANT, content=response.content))

            # If model requested tool calls, execute them
            if response.has_tool_calls:
                for tool_call in response.tool_calls:
                    if self._check_cancelled(cancellation_token):
                        if self._agent.state != AgentState.TERMINATED:
                            self._agent.transition_to(
                                AgentState.TERMINATED,
                                metadata={"reason": "cancelled"},
                            )
                        return "Task cancelled"

                    try:
                        await self._agent.execute_tool(
                            tool_name=tool_call.name,
                            params=tool_call.arguments,
                            return_to_state=AgentState.THINKING,
                            tool_call_id=tool_call.id,
                        )
                    except asyncio.CancelledError:
                        if self._agent.state != AgentState.TERMINATED:
                            self._agent.transition_to(
                                AgentState.TERMINATED,
                                metadata={"reason": "cancelled"},
                            )
                        raise
                continue

            # No tool calls — final response
            if self._agent.state == AgentState.THINKING:
                self._agent.transition_to(AgentState.IDLE)
            return response.content or "Done"

        # Max steps exceeded
        if self._agent.state != AgentState.TERMINATED:
            self._agent.transition_to(
                AgentState.ERROR,
                metadata={"reason": "max_steps_exceeded"},
            )
        return "Maximum steps exceeded"

    async def _think(self) -> ModelResponse:
        """Call the model provider for the next reasoning step."""
        provider: LLMProvider = self._agent.model_provider

        messages = self._agent.history.get_messages()
        req = ModelRequest(
            model="router",
            messages=messages,
            max_tokens=1024,
            temperature=self._agent.config.temperature,
        )

        return await provider.generate(req)

    def _check_cancelled(
        self,
        cancellation_token: CancellationToken | None,
    ) -> bool:
        """Check cancellation token and transition agent if needed.

        Returns True if cancellation was requested.
        """
        if cancellation_token is not None and cancellation_token.is_cancelled():
            if self._agent.state != AgentState.TERMINATED:
                self._agent.transition_to(
                    AgentState.TERMINATED,
                    metadata={"reason": "cancelled"},
                )
            return True
        return False
