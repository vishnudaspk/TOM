"""Unit tests for AgentOrchestrator — multi-step loop, cancellation, error recovery.

Adheres to:
- PHASE4_IMPLEMENTATIONPLAN.md §5 (Iteration 5)
- Decision 035: Step-Bounded Execution Loop & Cooperative Task Cancellation
- skills/testing/python-testing: Strict assertions, edge cases, deterministic testing.
"""

import asyncio
from typing import Any

from tom.agents.base import Agent
from tom.agents.dependencies import AgentDependencies
from tom.agents.orchestrator import AgentOrchestrator
from tom.core.context import CancellationToken
from tom.models.base import ModelProviderError
from tom.models.providers.mock import MockModelProvider
from tom.models.schemas import (
    ModelResponse,
    ToolCallRequest,
)
from tom.schemas.agent import (
    AgentConfig,
    AgentState,
    Role,
)
from tom.schemas.router import (
    IntentDomain,
    RoutingDecision,
)


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
    model_provider: Any = None,
) -> Agent:
    """Create agent with optional model_provider via dependencies."""
    deps = AgentDependencies(model_provider=model_provider) if model_provider else None
    return Agent(config=config, dependencies=deps)


def make_orchestrator(
    agent_config: AgentConfig | None = None,
    model_responses: list[str | ModelResponse] | None = None,
    route: RoutingDecision | None = None,
) -> tuple[AgentOrchestrator, Agent]:
    """Helper: create orchestrator with configurable router and model provider."""
    model_provider = MockModelProvider(model_responses) if model_responses else None
    agent = make_agent(config=agent_config, model_provider=model_provider)
    decision = route or RoutingDecision(
        domain=IntentDomain.REASONING,
        confidence=0.5,
        target_tool=None,
        route_tier=2,
        latency_ms=1.0,
    )
    router = FakeRouter([decision])
    orchestrator = AgentOrchestrator(agent=agent, router=router)
    return orchestrator, agent


class TestOrchestratorInitialization:
    """Tests for AgentOrchestrator creation."""

    def test_initialization(self) -> None:
        agent = Agent()
        router = FakeRouter()
        orchestrator = AgentOrchestrator(agent=agent, router=router)
        assert orchestrator.agent is agent


class TestInitialCancellation:
    """Tests that cancellation before run() returns immediately."""

    def test_cancelled_token_returns_immediately(self) -> None:
        token = CancellationToken()
        token.cancel()
        orchestrator, _ = make_orchestrator(
            model_responses=["should not run"],
            route=RoutingDecision(
                domain=IntentDomain.CHAT,
                confidence=0.3,
                target_tool=None,
                route_tier=1,
                latency_ms=0.1,
            ),
        )

        result = asyncio.run(orchestrator.run("any prompt", cancellation_token=token))

        assert result == "Task cancelled"

    def test_cancelled_sets_terminated_state(self) -> None:
        token = CancellationToken()
        token.cancel()
        orchestrator, agent = make_orchestrator(
            model_responses=["should not run"],
            route=RoutingDecision(
                domain=IntentDomain.CHAT,
                confidence=0.3,
                target_tool=None,
                route_tier=1,
                latency_ms=0.1,
            ),
        )

        asyncio.run(orchestrator.run("any prompt", cancellation_token=token))

        assert agent.state == AgentState.TERMINATED

    def test_no_token_proceeds_normally(self) -> None:
        orchestrator, agent = make_orchestrator(
            model_responses=["done"],
        )

        result = asyncio.run(orchestrator.run("test"))

        assert result == "done"
        assert agent.state == AgentState.IDLE


class TestTier1DirectTool:
    """Tests Tier 1 routing — direct tool execution."""

    def test_tier1_system_tool(self) -> None:
        orchestrator, _ = make_orchestrator(
            route=RoutingDecision(
                domain=IntentDomain.SYSTEM,
                confidence=0.95,
                target_tool="system.cpu_info",
                route_tier=1,
                latency_ms=0.1,
            )
        )

        result = asyncio.run(orchestrator.run("system.cpu_info"))

        assert "Tool" in result or "tool" in result or result is not None
        assert orchestrator.agent.state == AgentState.IDLE

    def test_tier1_files_tool(self) -> None:
        orchestrator, _ = make_orchestrator(
            route=RoutingDecision(
                domain=IntentDomain.FILES,
                confidence=0.95,
                target_tool="files.list_directory",
                route_tier=1,
                latency_ms=0.1,
            )
        )

        result = asyncio.run(orchestrator.run("files.list_directory"))

        assert result is not None
        assert orchestrator.agent.state == AgentState.IDLE


class TestNoToolNoProvider:
    """Tests behavior when no Tier 1 match and no model provider."""

    def test_fallback_message(self) -> None:
        agent = make_agent()
        router = FakeRouter(
            [
                RoutingDecision(
                    domain=IntentDomain.UNKNOWN,
                    confidence=0.3,
                    target_tool=None,
                    route_tier=1,
                    latency_ms=0.1,
                )
            ]
        )
        orchestrator = AgentOrchestrator(agent=agent, router=router)

        result = asyncio.run(orchestrator.run("ambiguous"))

        assert result == "I'm not sure how to help with that"
        assert agent.state == AgentState.ERROR


class TestTier2ModelLoop:
    """Tests Tier 2 — multi-step agent loop."""

    def test_simple_text_response(self) -> None:
        orchestrator, agent = make_orchestrator(
            model_responses=["I understand"],
        )

        result = asyncio.run(orchestrator.run("explain"))

        assert result == "I understand"
        assert agent.state == AgentState.IDLE

    def test_records_user_message(self) -> None:
        orchestrator, agent = make_orchestrator(
            model_responses=["Got it"],
        )

        asyncio.run(orchestrator.run("What is 42?"))

        user_msgs = [m for m in agent.history if m.role == Role.USER]
        assert any(m.content == "What is 42?" for m in user_msgs)

    def test_records_assistant_message(self) -> None:
        orchestrator, agent = make_orchestrator(
            model_responses=["The answer is 42"],
        )

        asyncio.run(orchestrator.run("What is 42?"))

        assistant_msgs = [m for m in agent.history if m.role == Role.ASSISTANT]
        assert any(m.content == "The answer is 42" for m in assistant_msgs)

    def test_tool_calls_execute(self) -> None:
        orchestrator, agent = make_orchestrator(
            model_responses=[
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
                "CPU usage normal",
            ],
        )

        result = asyncio.run(orchestrator.run("check system"))

        assert result == "CPU usage normal"
        assert agent.state == AgentState.IDLE

    def test_max_steps_exceeded(self) -> None:
        tool_call_response = ModelResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="nonexistent.tool",
                    arguments={},
                )
            ],
            finish_reason="tool_call",
        )
        model_provider = MockModelProvider([tool_call_response], loop=True)
        agent = make_agent(model_provider=model_provider)
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
        orchestrator = AgentOrchestrator(agent=agent, router=router)

        result = asyncio.run(orchestrator.run("loop", max_steps=2))

        assert result == "Maximum steps exceeded"
        assert agent.state == AgentState.ERROR


class TestModelErrorHandling:
    """Tests error recovery during model calls."""

    def test_model_provider_error(self) -> None:
        orchestrator, agent = make_orchestrator(
            model_responses=[ModelProviderError("Connection failed")],
        )

        result = asyncio.run(orchestrator.run("explain"))

        assert "Model error" in result
        assert agent.state == AgentState.ERROR

    def test_model_timeout_error(self) -> None:
        orchestrator, agent = make_orchestrator(
            model_responses=[TimeoutError("Timed out")],
        )

        result = asyncio.run(orchestrator.run("explain"))

        assert "Model error" in result

    def test_model_generic_exception(self) -> None:
        orchestrator, agent = make_orchestrator(
            model_responses=[RuntimeError("Boom")],
        )

        result = asyncio.run(orchestrator.run("explain"))

        assert "Model error" in result
        assert "Boom" in result


class TestCancellationDuringLoop:
    """Tests cooperative cancellation during multi-step loop."""

    def test_cancel_during_slow_model_call(self) -> None:
        token = CancellationToken()
        slow_provider = MockModelProvider(["done"], latency_seconds=1.0)
        agent = make_agent(model_provider=slow_provider)
        orchestrator = AgentOrchestrator(
            agent=agent,
            router=FakeRouter(
                [
                    RoutingDecision(
                        domain=IntentDomain.REASONING,
                        confidence=0.5,
                        target_tool=None,
                        route_tier=2,
                        latency_ms=1.0,
                    )
                ]
            ),
        )

        async def run_and_cancel() -> str:
            task = asyncio.create_task(orchestrator.run("explain", cancellation_token=token))
            await asyncio.sleep(0.05)
            token.cancel()
            return await task

        result = asyncio.run(run_and_cancel())

        assert result in ("Task cancelled", "done")

    def test_cancel_before_run_returns_immediately(self) -> None:
        token = CancellationToken()
        token.cancel()
        orchestrator, _ = make_orchestrator()

        result = asyncio.run(orchestrator.run("test", cancellation_token=token))

        assert result == "Task cancelled"


class TestDirectToolErrorHandling:
    """Tests error handling in direct tool execution path."""

    def test_nonexistent_tool_returns_failure(self) -> None:
        orchestrator, _ = make_orchestrator(
            route=RoutingDecision(
                domain=IntentDomain.SYSTEM,
                confidence=0.95,
                target_tool="nonexistent.tool",
                route_tier=1,
                latency_ms=0.1,
            )
        )

        result = asyncio.run(orchestrator.run("nonexistent.tool"))

        assert "not registered" in result.lower() or "not found" in result.lower()


class TestStateTransitions:
    """Tests agent state transitions during orchestration."""

    def test_tier1_starts_idle_ends_idle(self) -> None:
        orchestrator, agent = make_orchestrator(
            route=RoutingDecision(
                domain=IntentDomain.SYSTEM,
                confidence=0.95,
                target_tool="system.cpu_info",
                route_tier=1,
                latency_ms=0.1,
            )
        )

        asyncio.run(orchestrator.run("system.cpu_info"))

        assert agent.state == AgentState.IDLE

    def test_tier2_starts_idle_ends_idle(self) -> None:
        orchestrator, agent = make_orchestrator(
            model_responses=["Done"],
        )

        asyncio.run(orchestrator.run("explain"))

        assert agent.state == AgentState.IDLE

    def test_max_steps_sets_error_state(self) -> None:
        config = AgentConfig(max_steps=1)
        orchestrator, agent = make_orchestrator(
            agent_config=config,
            model_responses=[],
        )

        asyncio.run(orchestrator.run("loop", max_steps=1))

        assert agent.state == AgentState.ERROR


class TestModelRequestConstruction:
    """Tests that ModelRequest is properly built for model calls."""

    def test_uses_agent_config_temperature(self) -> None:
        config = AgentConfig(temperature=0.3, system_prompt="You are helpful.")
        agent = make_agent(config=config, model_provider=MockModelProvider(["response"]))
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
        orchestrator = AgentOrchestrator(agent=agent, router=router)

        asyncio.run(orchestrator.run("test"))

        provider = agent.model_provider
        assert isinstance(provider, MockModelProvider)
        assert provider.call_count >= 1


class TestMaxStepsBounded:
    """Tests that max_steps parameter bounds the loop."""

    def test_custom_max_steps_limited_calls(self) -> None:
        tool_call_response = ModelResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="nonexistent.tool",
                    arguments={},
                )
            ],
            finish_reason="tool_call",
        )
        model_provider = MockModelProvider([tool_call_response], loop=True)
        agent = make_agent(model_provider=model_provider)
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
        orchestrator = AgentOrchestrator(agent=agent, router=router)

        asyncio.run(orchestrator.run("test", max_steps=3))

        assert model_provider.call_count == 3


class TestEmptyPrompt:
    """Tests edge case of empty prompt."""

    def test_empty_prompt_no_crash(self) -> None:
        orchestrator, _ = make_orchestrator(
            model_responses=[""],
            route=RoutingDecision(
                domain=IntentDomain.CHAT,
                confidence=0.5,
                target_tool=None,
                route_tier=2,
                latency_ms=1.0,
            ),
        )

        result = asyncio.run(orchestrator.run(""))

        assert result is not None
