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
import sys
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from tom.agents.base import Agent, AgentState
from tom.agents.prompts import refresh_prompt_temporal_context
from tom.core.context import CancellationToken
from tom.core.router import IntentRouter
from tom.models.base import LLMProvider
from tom.models.providers.http import HttpModelProvider
from tom.models.schemas import ModelRequest, ModelResponse, StreamChunk, ToolCallRequest
from tom.resources.manager import ModelRole, ResourceManager
from tom.schemas.agent import Message, Role
from tom.schemas.config import TOMConfig
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
        resource_manager: ResourceManager | None = None,
        config: TOMConfig | None = None,
        max_response_tokens: int = 1024,
    ) -> None:
        self._agent = agent
        self._router = router
        self._resource_manager = resource_manager
        self._config = config
        self._max_response_tokens = max_response_tokens
        self._turn_lock = agent._conversation_lock

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
        async with self._turn_lock:
            original_history = self._agent.history
            staged_history = original_history.model_copy(deep=True)
            self._agent._history = staged_history
            try:
                result = await self._run_turn(prompt, cancellation_token, max_steps)
            finally:
                if self._agent.state == AgentState.IDLE:
                    original_history.messages = staged_history.messages
                self._agent._history = original_history
            return result

    async def _run_turn(
        self,
        prompt: str,
        cancellation_token: CancellationToken | None,
        max_steps: int,
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

        # Recover from previous turn error if needed
        if self._agent.state == AgentState.ERROR:
            self._agent.transition_to(AgentState.IDLE)

        self._refresh_temporal_context()

        # Route the request
        route: RoutingDecision = await self._router.classify(prompt)
        logger.info(
            "orchestrator_routed",
            domain=route.domain.value,
            route_tier=route.route_tier,
            router_ms=round(route.latency_ms, 2),
        )

        # Add user message to history
        turn_id = uuid4().hex
        self._agent.history.append(Message(role=Role.USER, content=prompt, turn_id=turn_id))

        # Tier 1: direct tool execution
        if route.target_tool is not None:
            return await self._execute_direct_tool(route.target_tool, turn_id)

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
            turn_id,
        )

    async def stream(
        self,
        prompt: str,
        token: CancellationToken | None = None,
    ) -> AsyncGenerator[str, None]:
        async with self._turn_lock:
            original_history = self._agent.history
            staged_history = original_history.model_copy(deep=True)
            self._agent._history = staged_history
            completed = False
            stream_iter = self._stream_turn(prompt, token)
            try:
                async for delta in stream_iter:
                    yield delta
                completed = True
            finally:
                await self._close_stream(stream_iter)
                if completed and self._agent.state == AgentState.IDLE:
                    original_history.messages = staged_history.messages
                self._agent._history = original_history

    async def _stream_turn(
        self,
        prompt: str,
        token: CancellationToken | None,
    ) -> AsyncGenerator[str, None]:
        """Yield incremental model text deltas for a user prompt.

        Preserves the same routing, tool execution, and agent-state path as
        ``run()``. When a tool call interrupts generation, already-buffered
        text is yielded first, then tools run through ``Agent.execute_tool``.
        """
        if self._check_cancelled(token):
            return

        if self._agent.state == AgentState.ERROR:
            self._agent.transition_to(AgentState.IDLE)

        self._refresh_temporal_context()

        route: RoutingDecision = await self._router.classify(prompt)
        logger.info(
            "orchestrator_routed",
            domain=route.domain.value,
            route_tier=route.route_tier,
            router_ms=round(route.latency_ms, 2),
        )
        turn_id = uuid4().hex
        self._agent.history.append(Message(role=Role.USER, content=prompt, turn_id=turn_id))

        if route.target_tool is not None:
            text = await self._execute_direct_tool(route.target_tool, turn_id)
            if text:
                yield text
            return

        if self._agent.model_provider is None:
            if self._agent.state != AgentState.TERMINATED:
                self._agent.transition_to(AgentState.THINKING)
                self._agent.transition_to(AgentState.ERROR)
            yield "I'm not sure how to help with that"
            return

        loop_gen = self._stream_agent_loop(prompt, token, _DEFAULT_MAX_STEPS, turn_id)
        try:
            async for delta in loop_gen:
                yield delta
        finally:
            await self._close_stream(loop_gen)

    async def _execute_tool_compat(
        self,
        *,
        tool_name: str,
        params: dict[str, Any] | BaseModel | None = None,
        return_to_state: AgentState = AgentState.THINKING,
        tool_call_id: str | None = None,
        turn_id: str | None = None,
    ) -> ToolResult:
        """Execute a tool while tolerating older test doubles that omit ``turn_id``."""
        call_kwargs: dict[str, Any] = {
            "tool_name": tool_name,
            "params": params,
            "return_to_state": return_to_state,
            "tool_call_id": tool_call_id,
        }
        if turn_id is not None:
            call_kwargs["turn_id"] = turn_id
        try:
            return await self._agent.execute_tool(**call_kwargs)
        except TypeError as exc:
            if "turn_id" not in str(exc):
                raise
            call_kwargs.pop("turn_id", None)
            return await self._agent.execute_tool(**call_kwargs)

    async def _close_stream(self, stream_iter: Any | None) -> None:
        """Close a provider stream without releasing a heavy lease early.

        If the caller is being cancelled, wait for the provider's own cleanup to
        complete before re-raising cancellation. This preserves the resource
        governor invariant that a heavy lease stays active until the underlying
        model stream has fully terminated.
        """
        if stream_iter is None or not hasattr(stream_iter, "aclose"):
            return

        close_task = asyncio.create_task(stream_iter.aclose())
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            await close_task
            raise

    async def _execute_direct_tool(self, tool_name: str, turn_id: str | None = None) -> str:
        """Execute a single tool and return its result text."""
        if self._agent.state == AgentState.ERROR:
            self._agent.transition_to(AgentState.IDLE)
        if self._agent.state == AgentState.IDLE:
            self._agent.transition_to(AgentState.THINKING)

        result: ToolResult = await self._execute_tool_compat(
            tool_name=tool_name,
            params=None,
            return_to_state=AgentState.THINKING,
            tool_call_id=None,
            turn_id=turn_id,
        )

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
        turn_id: str,
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
            self._agent.history.append(
                Message(
                    role=Role.ASSISTANT,
                    content=response.content,
                    turn_id=turn_id,
                    tool_calls=response.tool_calls if response.has_tool_calls else None,
                )
            )

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
                        await self._execute_tool_compat(
                            tool_name=tool_call.name,
                            params=tool_call.arguments,
                            return_to_state=AgentState.THINKING,
                            tool_call_id=tool_call.id,
                            turn_id=turn_id,
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
        tools_schema = (
            self._agent.dependencies.registry.export_json_schemas()
            if hasattr(self._agent, "dependencies")
            and hasattr(self._agent.dependencies, "registry")
            else None
        )
        req = ModelRequest(
            model="router",
            messages=messages,
            max_tokens=self._max_response_tokens,
            temperature=self._agent.config.temperature,
            tools=tools_schema,
        )

        if self._resource_manager is not None:
            # Get VRAM requirement from loaded config (single source of truth)
            # for BOTH resident registration and lease acquisition.
            # Use an explicit lease lifecycle so the heavy lock stays held until
            # the provider has fully finished any in-flight cleanup.
            reasoning_vram_mb = (
                self._config.models.reasoning.vram_required_mb if self._config else 4531
            )
            await self._maybe_register_resident_model(ModelRole.REASONING, reasoning_vram_mb)
            lease = await self._resource_manager.acquire(
                ModelRole.REASONING, required_vram_mb=reasoning_vram_mb
            )
            try:
                return await provider.generate(req)
            finally:
                await lease.release()
        return await provider.generate(req)

    async def _maybe_register_resident_model(self, role: ModelRole, required_vram_mb: int) -> None:
        """Check if the model for the given role is resident in llama-server and register it."""
        if self._resource_manager is None:
            return
        # Only check for heavy roles that use the llama-server
        if role not in (ModelRole.REASONING, ModelRole.VLM):
            return
        # Avoid re-checking if already registered
        if self._resource_manager.get_resident_vram(role) > 0:
            return
        # Check if provider is HttpModelProvider (has list_models)
        provider = self._agent.model_provider
        if not isinstance(provider, HttpModelProvider):
            return
        try:
            models = await provider.list_models()
            # Check if any model is loaded (llama-server returns loaded models in /v1/models)
            if models:
                # Model is resident - register its VRAM usage
                self._resource_manager.register_resident_model(role, required_vram_mb)
                logger.info("model_resident_detected", role=str(role), model_count=len(models))
        except Exception as exc:
            logger.debug("residency_check_failed", role=str(role), error=str(exc))

    async def _think_stream(self) -> AsyncIterator[StreamChunk]:
        """Call the model provider's streaming completion.

        Returns an async iterator from the provider. The caller is responsible
        for managing the resource lease (if any) around the iteration.
        """
        provider: LLMProvider = self._agent.model_provider
        messages = self._agent.history.get_messages()
        tools_schema = (
            self._agent.dependencies.registry.export_json_schemas()
            if hasattr(self._agent, "dependencies")
            and hasattr(self._agent.dependencies, "registry")
            else None
        )
        req = ModelRequest(
            model="router",
            messages=messages,
            max_tokens=self._max_response_tokens,
            temperature=self._agent.config.temperature,
            stream=True,
            tools=tools_schema,
        )
        return provider.stream(req)

    async def _stream_agent_loop(
        self,
        prompt: str,
        token: CancellationToken | None,
        max_steps: int,
        turn_id: str,
    ) -> AsyncGenerator[str, None]:
        """Stream tokens; pause for tools; resume generation."""
        for step in range(max_steps):
            if self._check_cancelled(token):
                return

            if (
                self._agent.state != AgentState.TERMINATED
                and self._agent.state != AgentState.THINKING
            ):
                self._agent.transition_to(
                    AgentState.THINKING,
                    metadata={"step": step + 1},
                )

            collected: list[str] = []
            tool_calls: list[ToolCallRequest] = []
            stream_closing = False
            try:
                if self._resource_manager is not None:
                    reasoning_vram_mb = (
                        self._config.models.reasoning.vram_required_mb if self._config else 4531
                    )
                    await self._maybe_register_resident_model(
                        ModelRole.REASONING, reasoning_vram_mb
                    )
                    lease = await self._resource_manager.acquire(
                        ModelRole.REASONING, required_vram_mb=reasoning_vram_mb
                    )
                    stream_iter = None
                    try:
                        stream_iter = await self._think_stream()
                        async for chunk in stream_iter:
                            if self._check_cancelled(token):
                                if collected:
                                    self._agent.history.append(
                                        Message(role=Role.ASSISTANT, content="".join(collected), turn_id=turn_id)
                                    )
                                return
                            if chunk.content:
                                collected.append(chunk.content)
                                yield chunk.content
                            if chunk.tool_call_delta is not None:
                                tool_calls.append(chunk.tool_call_delta)
                    finally:
                        stream_closing = sys.exc_info()[0] is GeneratorExit
                        try:
                            await self._close_stream(stream_iter)
                        finally:
                            await lease.release()
                else:
                    stream_iter = await self._think_stream()
                    try:
                        async for chunk in stream_iter:
                            if self._check_cancelled(token):
                                if collected:
                                    self._agent.history.append(
                                        Message(role=Role.ASSISTANT, content="".join(collected), turn_id=turn_id)
                                    )
                                return
                            if chunk.content:
                                collected.append(chunk.content)
                                yield chunk.content
                            if chunk.tool_call_delta is not None:
                                tool_calls.append(chunk.tool_call_delta)
                    finally:
                        stream_closing = sys.exc_info()[0] is GeneratorExit
                        await self._close_stream(stream_iter)
            except asyncio.CancelledError:
                if self._agent.state != AgentState.TERMINATED:
                    self._agent.transition_to(
                        AgentState.TERMINATED,
                        metadata={"reason": "cancelled"},
                    )
                raise
            except Exception as exc:
                if stream_closing:
                    raise
                logger.error(
                    "orchestrator_model_error",
                    step=step + 1,
                    error=str(exc),
                )
                if self._agent.state == AgentState.THINKING:
                    self._agent.transition_to(AgentState.ERROR)
                yield f"Model error: {exc}"
                return

            self._agent.history.append(
                Message(
                    role=Role.ASSISTANT,
                    content="".join(collected),
                    turn_id=turn_id,
                    tool_calls=tool_calls if tool_calls else None,
                )
            )

            if tool_calls:
                for tool_call in tool_calls:
                    if self._check_cancelled(token):
                        if self._agent.state != AgentState.TERMINATED:
                            self._agent.transition_to(
                                AgentState.TERMINATED,
                                metadata={"reason": "cancelled"},
                            )
                        return
                    try:
                        await self._execute_tool_compat(
                            tool_name=tool_call.name,
                            params=tool_call.arguments,
                            return_to_state=AgentState.THINKING,
                            tool_call_id=tool_call.id,
                            turn_id=turn_id,
                        )
                    except asyncio.CancelledError:
                        if self._agent.state != AgentState.TERMINATED:
                            self._agent.transition_to(
                                AgentState.TERMINATED,
                                metadata={"reason": "cancelled"},
                            )
                        raise
                continue

            if self._agent.state == AgentState.THINKING:
                self._agent.transition_to(AgentState.IDLE)
            return

        if self._agent.state != AgentState.TERMINATED:
            self._agent.transition_to(
                AgentState.ERROR,
                metadata={"reason": "max_steps_exceeded"},
            )

    def _refresh_temporal_context(self) -> None:
        """Keep the leading system prompt anchored with current local time and date."""
        messages = self._agent.history.messages
        if messages and messages[0].role == Role.SYSTEM:
            content = messages[0].content or ""
            refreshed = refresh_prompt_temporal_context(content)
            if refreshed != content:
                messages[0] = Message(role=Role.SYSTEM, content=refreshed)
        elif not messages and self._agent.config.system_prompt:
            refreshed = refresh_prompt_temporal_context(self._agent.config.system_prompt)
            self._agent.history.append(Message(role=Role.SYSTEM, content=refreshed))

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
