"""Unit tests for Phase 9 Iteration 9.2: AgentOrchestrator.stream()."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

from tom.agents.base import Agent
from tom.agents.dependencies import AgentDependencies
from tom.agents.orchestrator import AgentOrchestrator
from tom.core.context import CancellationToken
from tom.models.providers.mock import MockModelProvider
from tom.models.schemas import ModelResponse, ToolCallRequest
from tom.schemas.agent import AgentState, Role
from tom.schemas.router import IntentDomain, RoutingDecision
from tom.tools.registry import ToolResult


class FakeRouter:
    def __init__(self, responses: list[RoutingDecision] | None = None) -> None:
        self._responses: list[RoutingDecision] = responses or []
        self._call_count = 0

    async def classify(self, user_input: str) -> RoutingDecision:
        del user_input
        if self._call_count >= len(self._responses):
            raise IndexError("FakeRouter: no more responses")
        response = self._responses[self._call_count]
        self._call_count += 1
        return response


def _tier2() -> RoutingDecision:
    return RoutingDecision(
        domain=IntentDomain.REASONING,
        confidence=0.5,
        target_tool=None,
        route_tier=2,
        latency_ms=1.0,
    )


def make_orchestrator(
    model_responses: list[str | ModelResponse] | None = None,
    route: RoutingDecision | None = None,
) -> tuple[AgentOrchestrator, Agent]:
    model_provider = MockModelProvider(model_responses) if model_responses else None
    deps = AgentDependencies(model_provider=model_provider) if model_provider else None
    agent = Agent(dependencies=deps)
    router = FakeRouter([route or _tier2()])
    return AgentOrchestrator(agent=agent, router=router), agent


async def collect_stream(
    orchestrator: AgentOrchestrator,
    prompt: str,
    token: CancellationToken | None = None,
) -> list[str]:
    return [delta async for delta in orchestrator.stream(prompt, token)]


class TestStreamDeltas:
    def test_yields_incremental_mock_deltas(self) -> None:
        orchestrator, _ = make_orchestrator(model_responses=["Hello world today"])

        deltas = asyncio.run(collect_stream(orchestrator, "hi"))

        assert deltas == ["Hello ", "world ", "today"]

    def test_run_behavior_unchanged(self) -> None:
        orchestrator, agent = make_orchestrator(model_responses=["I understand"])

        result = asyncio.run(orchestrator.run("explain"))

        assert result == "I understand"
        assert agent.state == AgentState.IDLE
        assert any(m.role == Role.ASSISTANT and m.content == "I understand" for m in agent.history)


class TestStreamCancellation:
    def test_cancel_stops_cooperatively(self) -> None:
        token = CancellationToken()
        provider = MockModelProvider(
            ["alpha bravo charlie delta echo"],
            latency_seconds=0.15,
        )
        agent = Agent(dependencies=AgentDependencies(model_provider=provider))
        orchestrator = AgentOrchestrator(agent=agent, router=FakeRouter([_tier2()]))

        async def consume() -> list[str]:
            got: list[str] = []
            async for delta in orchestrator.stream("go", token):
                got.append(delta)
                token.cancel()
            return got

        got = asyncio.run(consume())
        assert 0 < len(got) < 5
        assert agent.state == AgentState.TERMINATED


class TestStreamToolPause:
    def test_yields_text_then_existing_tool_path_then_continues(self) -> None:
        orchestrator, agent = make_orchestrator(
            model_responses=[
                ModelResponse(
                    content="Let me check.",
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
            ]
        )

        order: list[str] = []
        original = agent.execute_tool

        async def tracked(*args: Any, **kwargs: Any) -> ToolResult:
            order.append(f"tool:{kwargs.get('tool_name') or args[0]}")
            return await original(*args, **kwargs)

        async def run() -> list[str]:
            with patch.object(agent, "execute_tool", tracked):
                deltas: list[str] = []
                async for delta in orchestrator.stream("check system"):
                    order.append(f"delta:{delta}")
                    deltas.append(delta)
                return deltas

        deltas = asyncio.run(run())

        tool_idx = order.index("tool:system.cpu_info")
        first_delta_idx = next(i for i, x in enumerate(order) if x.startswith("delta:"))
        assert first_delta_idx < tool_idx
        assert any(i > tool_idx and x.startswith("delta:") for i, x in enumerate(order))
        assert "".join(deltas).startswith("Let me check.")
        assert "CPU usage normal" in "".join(deltas)
        assert any(m.role == Role.TOOL_RESULT for m in agent.history)
        assert agent.state == AgentState.IDLE
