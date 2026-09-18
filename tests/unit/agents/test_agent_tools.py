"""Unit tests for Agent tool system integration, safety, confirmation, and error containment.

Adheres to:
- PHASE4_IMPLEMENTATIONPLAN.md §5 (Iteration 2)
- Decision 032: Centralized Tool Invocation Safety (Strict ToolExecutor Routing)
- skills/python/tool-system, skills/security/permission-model, skills/testing/python-testing
"""

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel, Field
from tom.agents.base import Agent
from tom.agents.dependencies import AgentDependencies
from tom.schemas.agent import (
    AgentState,
    Role,
)
from tom.security.confirmation import (
    AlwaysAllowConfirmationHook,
    AlwaysDenyConfirmationHook,
)
from tom.security.permissions import (
    PermissionEngine,
    PermissionLevel,
)
from tom.tools.bootstrap import setup_default_tools
from tom.tools.executor import ToolExecutor
from tom.tools.registry import (
    ToolRegistry,
    tool,
)


def run_async(coro: Any) -> Any:
    """Helper to run async coroutines in tests adhering to project standard."""
    return asyncio.run(coro)


@pytest.fixture
def clean_registry() -> ToolRegistry:
    """Isolated clean registry populated with default system and file tools."""
    reg = ToolRegistry()
    setup_default_tools(registry=reg)
    return reg


@pytest.fixture
def agent_with_tools(clean_registry: ToolRegistry) -> Agent:
    """Agent wired with an isolated tool registry and executor."""
    deps = AgentDependencies(registry=clean_registry)
    return Agent(dependencies=deps)


class TestSafeToolExecution:
    """Verifies SAFE tools execute cleanly and update Agent state and history."""

    def test_execute_system_cpu_info(self, agent_with_tools: Agent) -> None:
        agent = agent_with_tools
        assert agent.state == AgentState.IDLE

        result = run_async(agent.execute_tool("system.cpu_info"))

        assert result.success is True
        assert result.data is not None
        assert hasattr(result.data, "usage_percent")
        assert hasattr(result.data, "core_count")
        # Returned to default return_to_state (THINKING)
        assert agent.state == AgentState.THINKING

        # Verify observation appended to history
        assert len(agent.history) == 2  # System prompt + tool result
        obs = agent.history[1]
        assert obs.role == Role.TOOL_RESULT
        assert obs.name == "system.cpu_info"
        assert obs.tool_metadata is not None
        assert obs.tool_metadata.success is True
        assert obs.tool_metadata.tool_name == "system.cpu_info"

    def test_execute_with_custom_return_state_idle(self, agent_with_tools: Agent) -> None:
        agent = agent_with_tools
        result = run_async(
            agent.execute_tool(
                "system.cpu_info",
                return_to_state=AgentState.IDLE,
            )
        )
        assert result.success is True
        assert agent.state == AgentState.IDLE

    def test_execute_from_thinking_state(self, agent_with_tools: Agent) -> None:
        agent = agent_with_tools
        agent.transition_to(AgentState.THINKING)
        assert agent.state == AgentState.THINKING

        states_visited: list[AgentState] = []
        agent.add_state_callback(lambda evt: states_visited.append(evt.new_state))

        result = run_async(agent.execute_tool("system.cpu_info"))
        assert result.success is True
        assert agent.state == AgentState.THINKING
        assert states_visited == [AgentState.ACTING, AgentState.THINKING]


class TestConfirmationFlow:
    """Verifies interactive confirmation handling (ASK_USER) and state transitions."""

    def test_ask_user_confirmed_succeeds(self) -> None:
        reg = ToolRegistry()

        @tool(
            registry=reg,
            name="test.dangerous_op",
            permission_level=PermissionLevel.ASK_USER,
            description="Dangerous test tool",
        )
        def dangerous_op(param: str) -> str:
            return f"Executed with {param}"

        hook = AlwaysAllowConfirmationHook()
        executor = ToolExecutor(registry=reg, confirmation_hook=hook)
        deps = AgentDependencies(registry=reg, executor=executor)
        agent = Agent(dependencies=deps)

        states_visited: list[AgentState] = []
        agent.add_state_callback(lambda evt: states_visited.append(evt.new_state))

        result = run_async(agent.execute_tool("test.dangerous_op", {"param": "foo"}))

        assert result.success is True
        assert result.data == "Executed with foo"
        # Verify complete state transition sequence:
        # IDLE -> THINKING -> ACTING -> WAITING_CONFIRMATION -> ACTING -> THINKING
        assert states_visited == [
            AgentState.THINKING,
            AgentState.ACTING,
            AgentState.WAITING_CONFIRMATION,
            AgentState.ACTING,
            AgentState.THINKING,
        ]

        # History check
        obs = agent.history[-1]
        assert obs.role == Role.TOOL_RESULT
        assert obs.tool_metadata.success is True

    def test_ask_user_denied_contained(self) -> None:
        reg = ToolRegistry()

        @tool(
            registry=reg,
            name="test.mutate_data",
            permission_level=PermissionLevel.ASK_USER,
            description="Mutation test tool",
        )
        def mutate_data(value: int) -> int:
            return value * 2

        hook = AlwaysDenyConfirmationHook()
        executor = ToolExecutor(registry=reg, confirmation_hook=hook)
        deps = AgentDependencies(registry=reg, executor=executor)
        agent = Agent(dependencies=deps)

        states_visited: list[AgentState] = []
        agent.add_state_callback(lambda evt: states_visited.append(evt.new_state))

        # Must not raise an exception; error containment
        result = run_async(agent.execute_tool("test.mutate_data", {"value": 10}))

        assert result.success is False
        assert "User confirmation denied" in (result.error or "")
        # Visited WAITING_CONFIRMATION, then transitioned to IDLE, then THINKING
        assert AgentState.WAITING_CONFIRMATION in states_visited
        assert agent.state == AgentState.THINKING

        # Check recorded failure in history
        obs = agent.history[-1]
        assert obs.role == Role.TOOL_RESULT
        assert obs.name == "test.mutate_data"
        assert obs.tool_metadata.success is False
        assert "User confirmation denied" in obs.content


class TestBlockPolicyEnforcement:
    """Verifies that BLOCK policies are unconditionally rejected without crashing the agent."""

    def test_block_level_tool_rejected(self) -> None:
        reg = ToolRegistry()

        @tool(
            registry=reg,
            name="test.arbitrary_shell",
            permission_level=PermissionLevel.BLOCK,
            description="Prohibited shell tool",
        )
        def arbitrary_shell(cmd: str) -> str:
            return "should never run"

        engine = PermissionEngine()
        executor = ToolExecutor(registry=reg, permission_engine=engine)
        deps = AgentDependencies(registry=reg, executor=executor)
        agent = Agent(dependencies=deps)

        result = run_async(agent.execute_tool("test.arbitrary_shell", {"cmd": "rm -rf"}))

        assert result.success is False
        assert "prohibited by security policy" in (result.error or "")
        # Verify recorded in history
        obs = agent.history[-1]
        assert obs.role == Role.TOOL_RESULT
        assert obs.tool_metadata.success is False
        assert "prohibited by security policy" in obs.content


class TestErrorContainmentAndNonBypass:
    """Verifies that tool execution errors never crash the agent."""

    def test_nonexistent_tool_returns_failure(self, agent_with_tools: Agent) -> None:
        agent = agent_with_tools
        result = run_async(agent.execute_tool("nonexistent.tool_xyz"))

        assert result.success is False
        err = (result.error or "").lower()
        assert "not registered" in err or "not found" in err

        obs = agent.history[-1]
        assert obs.role == Role.TOOL_RESULT
        assert obs.tool_metadata.success is False
        obs_content = obs.content.lower()
        assert "not registered" in obs_content or "not found" in obs_content

    def test_argument_validation_failure_contained(self) -> None:
        reg = ToolRegistry()

        class StrictParams(BaseModel):
            count: int = Field(ge=1, le=10)

        @tool(
            registry=reg,
            name="test.strict_tool",
            input_schema=StrictParams,
            permission_level=PermissionLevel.SAFE,
        )
        def strict_tool(params: StrictParams) -> int:
            return params.count

        executor = ToolExecutor(registry=reg)
        deps = AgentDependencies(registry=reg, executor=executor)
        agent = Agent(dependencies=deps)

        # count=99 violates le=10
        result = run_async(agent.execute_tool("test.strict_tool", {"count": 99}))

        assert result.success is False
        assert "validation failed" in (result.error or "").lower()

        obs = agent.history[-1]
        assert obs.role == Role.TOOL_RESULT
        assert obs.tool_metadata.success is False

    def test_tool_handler_exception_contained(self) -> None:
        reg = ToolRegistry()

        @tool(
            registry=reg,
            name="test.exploding_tool",
            permission_level=PermissionLevel.SAFE,
        )
        def exploding_tool() -> None:
            raise RuntimeError("Database connection died")

        executor = ToolExecutor(registry=reg)
        deps = AgentDependencies(registry=reg, executor=executor)
        agent = Agent(dependencies=deps)

        result = run_async(agent.execute_tool("test.exploding_tool"))

        assert result.success is False
        assert "Database connection died" in (result.error or "")

        obs = agent.history[-1]
        assert obs.role == Role.TOOL_RESULT
        assert obs.tool_metadata.success is False
        assert "Database connection died" in obs.content


class TestCancellationSafety:
    """Verifies that cancelling an active tool call transitions the agent to TERMINATED."""

    def test_cancellation_during_tool_execution(self) -> None:
        reg = ToolRegistry()

        @tool(
            registry=reg,
            name="test.slow_tool",
            permission_level=PermissionLevel.SAFE,
        )
        async def slow_tool() -> str:
            await asyncio.sleep(5.0)
            return "done"

        executor = ToolExecutor(registry=reg)
        deps = AgentDependencies(registry=reg, executor=executor)
        agent = Agent(dependencies=deps)

        async def _run_and_cancel() -> None:
            task = asyncio.create_task(agent.execute_tool("test.slow_tool"))
            # Let it enter execution
            await asyncio.sleep(0.05)
            assert agent.state == AgentState.ACTING

            # Cancel caller task
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        run_async(_run_and_cancel())

        # Agent should have transitioned to TERMINATED
        assert agent.state == AgentState.TERMINATED
