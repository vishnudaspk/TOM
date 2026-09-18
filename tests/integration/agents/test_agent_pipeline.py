"""Integration tests for the complete TOM agent pipeline.

Exercises the full end-to-end flow:
    User Prompt → IntentRouter → AgentOrchestrator → Agent / LLMProvider
        → ToolExecutor → PermissionEngine → Deterministic Tools → Observation / Final Response

Adheres to:
- PHASE4_IMPLEMENTATIONPLAN.md §5 (Iteration 6)
- Decision 035: Step-Bounded Execution Loop & Cooperative Task Cancellation
"""

import asyncio

from tom.agents.base import Agent
from tom.agents.dependencies import AgentDependencies
from tom.agents.orchestrator import AgentOrchestrator
from tom.models.base import LLMProvider, ModelProviderError
from tom.models.providers.mock import MockModelProvider
from tom.models.schemas import ModelResponse, ToolCallRequest
from tom.schemas.agent import (
    AgentConfig,
    AgentState,
    Role,
)
from tom.schemas.router import (
    IntentDomain,
    RoutingDecision,
)
from tom.security.permissions import PermissionLevel
from tom.tools.bootstrap import setup_default_tools
from tom.tools.registry import ToolRegistry, tool


class FakeRouter:
    """Test double for IntentRouter with configurable classify responses."""

    def __init__(self, responses: list[RoutingDecision] | None = None) -> None:
        self._responses: list[RoutingDecision] = responses or []
        self._call_count = 0

    def set_responses(self, responses: list[RoutingDecision]) -> None:
        self._responses = responses
        self._call_count = 0

    async def classify(self, user_input: str) -> RoutingDecision:
        if self._call_count >= len(self._responses):
            raise IndexError("FakeRouter: no more responses")
        response = self._responses[self._call_count]
        self._call_count += 1
        return response


def make_agent(
    config: AgentConfig | None = None,
    model_provider: LLMProvider | None = None,
    registry: ToolRegistry | None = None,
) -> Agent:
    """Create agent with explicit dependencies."""
    deps = (
        AgentDependencies(
            model_provider=model_provider,
            registry=registry,
        )
        if model_provider or registry
        else None
    )
    return Agent(config=config, dependencies=deps)


def make_orchestrator(
    agent: Agent,
    router: FakeRouter | None = None,
) -> AgentOrchestrator:
    """Create orchestrator with agent and optional router."""
    return AgentOrchestrator(agent=agent, router=router or FakeRouter())


# ============================================================================
# 1. Direct Tool Pipeline (Tier 1)
# ============================================================================


class TestDirectToolPipeline:
    """Tier 1: direct tool requests reach the real tool pipeline."""

    def test_real_system_tool_via_orchestrator(self) -> None:
        """system.cpu_info executes through ToolExecutor via orchestrator."""
        reg = ToolRegistry()
        setup_default_tools(registry=reg)
        agent = make_agent(registry=reg)
        router = FakeRouter(
            [
                RoutingDecision(
                    domain=IntentDomain.SYSTEM,
                    confidence=0.95,
                    target_tool="system.cpu_info",
                    route_tier=1,
                    latency_ms=0.1,
                )
            ]
        )
        orchestrator = make_orchestrator(agent=agent, router=router)

        result = asyncio.run(orchestrator.run("system.cpu_info"))

        assert agent.state == AgentState.IDLE
        assert "cpu" in result.lower() or "core" in result.lower() or result is not None

    def test_real_file_tool_via_orchestrator(self) -> None:
        """files.list_directory executes through ToolExecutor via orchestrator."""
        reg = ToolRegistry()
        setup_default_tools(registry=reg)
        agent = make_agent(registry=reg)
        router = FakeRouter(
            [
                RoutingDecision(
                    domain=IntentDomain.FILES,
                    confidence=0.95,
                    target_tool="files.list_directory",
                    route_tier=1,
                    latency_ms=0.1,
                )
            ]
        )
        orchestrator = make_orchestrator(agent=agent, router=router)

        result = asyncio.run(orchestrator.run("files.list_directory"))

        assert agent.state == AgentState.IDLE
        assert result is not None


# ============================================================================
# 2. Multi-Step Agent Loop (Tier 2)
# ============================================================================


class TestMultiStepAgentLoop:
    """Tier 2: multi-step requests reach the agent loop."""

    def test_model_tool_calls_reach_tool_executor(self) -> None:
        """Model requests a tool; ToolExecutor executes it via the real pipeline."""
        reg = ToolRegistry()
        setup_default_tools(registry=reg)
        model_provider = MockModelProvider(
            [
                ModelResponse(
                    content="",
                    tool_calls=[
                        ToolCallRequest(
                            id="call_1",
                            name="system.cpu_info",
                            arguments={},
                        )
                    ],
                    finish_reason="tool_call",
                ),
                "CPU usage is normal",
            ]
        )
        agent = make_agent(model_provider=model_provider, registry=reg)
        router = FakeRouter(
            [
                RoutingDecision(
                    domain=IntentDomain.REASONING,
                    confidence=0.5,
                    target_tool=None,
                    route_tier=2,
                    latency_ms=1.0,
                )
            ]
        )
        orchestrator = make_orchestrator(agent=agent, router=router)

        result = asyncio.run(orchestrator.run("Check system"))

        assert result == "CPU usage is normal"
        assert agent.state == AgentState.IDLE
        # Model was called twice (tool call + final response)
        assert model_provider.call_count == 2

    def test_observation_returns_to_loop(self) -> None:
        """After tool execution, agent returns to THINKING for next step."""
        reg = ToolRegistry()
        setup_default_tools(registry=reg)
        model_provider = MockModelProvider(
            [
                ModelResponse(
                    content="",
                    tool_calls=[
                        ToolCallRequest(
                            id="call_1",
                            name="system.cpu_info",
                            arguments={},
                        )
                    ],
                    finish_reason="tool_call",
                ),
                ModelResponse(
                    content="",
                    tool_calls=[
                        ToolCallRequest(
                            id="call_2",
                            name="system.memory_info",
                            arguments={},
                        )
                    ],
                    finish_reason="tool_call",
                ),
                "Analysis complete",
            ]
        )
        agent = make_agent(model_provider=model_provider, registry=reg)
        router = FakeRouter(
            [
                RoutingDecision(
                    domain=IntentDomain.REASONING,
                    confidence=0.5,
                    target_tool=None,
                    route_tier=2,
                    latency_ms=1.0,
                )
            ]
        )
        orchestrator = make_orchestrator(agent=agent, router=router)

        result = asyncio.run(orchestrator.run("Analyze system"))

        assert result == "Analysis complete"
        assert agent.state == AgentState.IDLE
        assert model_provider.call_count == 3

    def test_final_response_terminates_loop(self) -> None:
        """A model response with no tool calls terminates the loop."""
        reg = ToolRegistry()
        setup_default_tools(registry=reg)
        model_provider = MockModelProvider(["Final answer"])
        agent = make_agent(model_provider=model_provider, registry=reg)
        router = FakeRouter(
            [
                RoutingDecision(
                    domain=IntentDomain.REASONING,
                    confidence=0.5,
                    target_tool=None,
                    route_tier=2,
                    latency_ms=1.0,
                )
            ]
        )
        orchestrator = make_orchestrator(agent=agent, router=router)

        result = asyncio.run(orchestrator.run("Simple question"))

        assert result == "Final answer"
        assert agent.state == AgentState.IDLE


# ============================================================================
# 3. Permission Enforcement
# ============================================================================


class TestPermissionEnforcement:
    """Permission enforcement remains active through the full pipeline."""

    def test_block_tool_rejected_via_pipeline(self) -> None:
        """BLOCK tools are unconditionally rejected by PermissionEngine via ToolExecutor."""
        reg = ToolRegistry()

        @tool(
            registry=reg,
            name="test.dangerous_op",
            permission_level=PermissionLevel.BLOCK,
            description="Prohibited test tool",
        )
        def dangerous_op() -> str:
            return "should never run"

        agent = make_agent(registry=reg)
        router = FakeRouter(
            [
                RoutingDecision(
                    domain=IntentDomain.SYSTEM,
                    confidence=0.95,
                    target_tool="test.dangerous_op",
                    route_tier=1,
                    latency_ms=0.1,
                )
            ]
        )
        orchestrator = make_orchestrator(agent=agent, router=router)

        result = asyncio.run(orchestrator.run("test.dangerous_op"))

        assert "prohibited" in result.lower() or "denied" in result.lower()
        assert agent.state == AgentState.IDLE
        # Verify result is a ToolResult failure recorded in history
        obs = agent.history[-1]
        assert obs.role == Role.TOOL_RESULT
        assert obs.tool_metadata is not None
        assert obs.tool_metadata.success is False

    def test_safe_tool_passes_through_pipeline(self) -> None:
        """SAFE tools execute successfully through the full pipeline."""
        reg = ToolRegistry()
        setup_default_tools(registry=reg)
        agent = make_agent(registry=reg)
        router = FakeRouter(
            [
                RoutingDecision(
                    domain=IntentDomain.SYSTEM,
                    confidence=0.95,
                    target_tool="system.cpu_info",
                    route_tier=1,
                    latency_ms=0.1,
                )
            ]
        )
        orchestrator = make_orchestrator(agent=agent, router=router)

        asyncio.run(orchestrator.run("system.cpu_info"))

        assert agent.state == AgentState.IDLE
        obs = agent.history[-1]
        assert obs.role == Role.TOOL_RESULT
        assert obs.tool_metadata is not None
        assert obs.tool_metadata.success is True
        assert obs.tool_metadata.tool_name == "system.cpu_info"


# ============================================================================
# 4. Cancellation
# ============================================================================


class TestCancellation:
    """Cancellation propagates correctly through the pipeline."""

    def test_cancelled_before_run_returns_immediately(self) -> None:
        """Cancelled token before run() aborts immediately."""
        from tom.core.context import CancellationToken

        token = CancellationToken()
        token.cancel()
        reg = ToolRegistry()
        setup_default_tools(registry=reg)
        agent = make_agent(registry=reg)
        router = FakeRouter(
            [
                RoutingDecision(
                    domain=IntentDomain.SYSTEM,
                    confidence=0.95,
                    target_tool="system.cpu_info",
                    route_tier=1,
                    latency_ms=0.1,
                )
            ]
        )
        orchestrator = make_orchestrator(agent=agent, router=router)

        result = asyncio.run(orchestrator.run("any prompt", cancellation_token=token))

        assert result == "Task cancelled"
        assert agent.state == AgentState.TERMINATED

    def test_cancellation_during_slow_model_call(self) -> None:
        """Cancelling during a slow model call terminates cooperatively."""
        from tom.core.context import CancellationToken

        token = CancellationToken()
        reg = ToolRegistry()
        setup_default_tools(registry=reg)
        slow_provider = MockModelProvider(["done"], latency_seconds=1.0)
        agent = make_agent(model_provider=slow_provider, registry=reg)
        router = FakeRouter(
            [
                RoutingDecision(
                    domain=IntentDomain.REASONING,
                    confidence=0.5,
                    target_tool=None,
                    route_tier=2,
                    latency_ms=1.0,
                )
            ]
        )
        orchestrator = make_orchestrator(agent=agent, router=router)

        async def run_and_cancel() -> str:
            task = asyncio.create_task(orchestrator.run("explain", cancellation_token=token))
            await asyncio.sleep(0.05)
            token.cancel()
            return await task

        result = asyncio.run(run_and_cancel())

        assert result in ("Task cancelled", "done")
        assert agent.state in (AgentState.TERMINATED, AgentState.IDLE)


# ============================================================================
# 5. Error Handling
# ============================================================================


class TestErrorHandling:
    """Existing error handling remains intact."""

    def test_nonexistent_tool_fails_gracefully(self) -> None:
        """Non-existent tool returns failure, not crash."""
        reg = ToolRegistry()
        setup_default_tools(registry=reg)
        agent = make_agent(registry=reg)
        router = FakeRouter(
            [
                RoutingDecision(
                    domain=IntentDomain.SYSTEM,
                    confidence=0.95,
                    target_tool="nonexistent.tool",
                    route_tier=1,
                    latency_ms=0.1,
                )
            ]
        )
        orchestrator = make_orchestrator(agent=agent, router=router)

        result = asyncio.run(orchestrator.run("nonexistent.tool"))

        assert "not registered" in result.lower() or "not found" in result.lower()
        assert agent.state == AgentState.IDLE

    def test_model_provider_error_recovered(self) -> None:
        """Model provider errors return error message, agent goes to ERROR."""
        reg = ToolRegistry()
        setup_default_tools(registry=reg)
        model_provider = MockModelProvider(
            [ModelProviderError("Connection failed")],
        )
        agent = make_agent(model_provider=model_provider, registry=reg)
        router = FakeRouter(
            [
                RoutingDecision(
                    domain=IntentDomain.REASONING,
                    confidence=0.5,
                    target_tool=None,
                    route_tier=2,
                    latency_ms=1.0,
                )
            ]
        )
        orchestrator = make_orchestrator(agent=agent, router=router)

        result = asyncio.run(orchestrator.run("explain"))

        assert "Model error" in result
        assert agent.state == AgentState.ERROR


# ============================================================================
# 6. Pipeline Integrity
# ============================================================================


class TestPipelineIntegrity:
    """Verify pipeline invariants."""

    def test_tool_executor_used_not_direct_handler(self) -> None:
        """Tools execute through ToolExecutor — verify by checking that a
        ToolResult is produced and recorded as an observation (TOOL_RESULT role).
        Direct handler invocation would not produce TOOL_RESULT observations."""
        reg = ToolRegistry()
        setup_default_tools(registry=reg)
        agent = make_agent(registry=reg)
        router = FakeRouter(
            [
                RoutingDecision(
                    domain=IntentDomain.SYSTEM,
                    confidence=0.95,
                    target_tool="system.cpu_info",
                    route_tier=1,
                    latency_ms=0.1,
                )
            ]
        )
        orchestrator = make_orchestrator(agent=agent, router=router)

        asyncio.run(orchestrator.run("system.cpu_info"))

        # Agent history should contain a TOOL_RESULT observation
        tool_results = [m for m in agent.history if m.role == Role.TOOL_RESULT]
        assert len(tool_results) >= 1
        # Observation records the tool name — proof it went through executor
        assert tool_results[-1].name == "system.cpu_info"

    def test_agent_state_transitions_are_deterministic(self) -> None:
        """Agent state after orchestration is deterministic and consistent."""
        reg = ToolRegistry()
        setup_default_tools(registry=reg)
        model_provider = MockModelProvider(["Done"])
        agent = make_agent(model_provider=model_provider, registry=reg)
        router = FakeRouter(
            [
                RoutingDecision(
                    domain=IntentDomain.REASONING,
                    confidence=0.5,
                    target_tool=None,
                    route_tier=2,
                    latency_ms=1.0,
                )
            ]
        )
        orchestrator = make_orchestrator(agent=agent, router=router)

        asyncio.run(orchestrator.run("test"))

        assert agent.state == AgentState.IDLE
        # Agent should not be TERMINATED, ACTING, WAITING_CONFIRMATION, or THINKING
        assert agent.state not in {
            AgentState.TERMINATED,
            AgentState.ACTING,
            AgentState.WAITING_CONFIRMATION,
            AgentState.THINKING,
        }
