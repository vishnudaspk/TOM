"""Performance benchmarks for TOM Phase 4 components."""

import asyncio
import sys
import time

sys.path.insert(0, "python")

from tom.agents.base import Agent
from tom.agents.dependencies import AgentDependencies
from tom.agents.orchestrator import AgentOrchestrator
from tom.core.router import IntentRouter
from tom.models.providers.mock import MockModelProvider
from tom.schemas.router import IntentDomain, RoutingDecision
from tom.security.permissions import PermissionEngine
from tom.tools.bootstrap import setup_default_tools
from tom.tools.executor import ToolExecutor
from tom.tools.registry import ToolRegistry


class FakeRouter:
    def __init__(self, responses):
        self._responses = responses or []
        self._call_count = 0

    async def classify(self, user_input):
        r = self._responses[self._call_count % len(self._responses)]
        self._call_count += 1
        return r


def benchmark_router_tier1():
    router = IntentRouter()
    iterations = 1000
    start = time.perf_counter()
    for _ in range(iterations):
        asyncio.run(router.classify("system.cpu_info"))
    elapsed = (time.perf_counter() - start) * 1000.0
    avg_ms = elapsed / iterations
    print(f"Tier 1 heuristic: avg {avg_ms:.4f} ms ({iterations} iterations)")
    print("Target: < 1 ms per call")
    print(f"Status: {'PASS' if avg_ms < 1.0 else 'FAIL'}")


def benchmark_router_tier2():
    router = IntentRouter(model_provider=MockModelProvider(["REASONING"], loop=True))
    iterations = 100
    start = time.perf_counter()
    for _ in range(iterations):
        asyncio.run(router.classify("explain quantum computing"))
    elapsed = (time.perf_counter() - start) * 1000.0
    avg_ms = elapsed / iterations
    print(f"Tier 2 model-backed (mock): avg {avg_ms:.4f} ms ({iterations} iterations)")
    print("Target: < 350 ms per call")
    print(f"Status: {'PASS' if avg_ms < 350.0 else 'FAIL'}")


def benchmark_tool_dispatch():
    reg = ToolRegistry()
    setup_default_tools(registry=reg)
    executor = ToolExecutor(registry=reg, permission_engine=PermissionEngine())
    iterations = 1000
    asyncio.run(executor.execute("system.cpu_info"))
    start = time.perf_counter()
    for _ in range(iterations):
        asyncio.run(executor.execute("system.cpu_info"))
    elapsed = (time.perf_counter() - start) * 1000.0
    avg_ms = elapsed / iterations
    print(f"Tool dispatch: avg {avg_ms:.4f} ms ({iterations} iterations)")
    print("Target: < 1 ms per call")
    print(f"Status: {'PASS' if avg_ms < 1.0 else 'FAIL'}")


def benchmark_agent_dispatch():
    model_provider = MockModelProvider(["Done"], loop=True)
    deps = AgentDependencies(model_provider=model_provider)
    agent = Agent(dependencies=deps)
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
    iterations = 50
    asyncio.run(orchestrator.run("test"))
    start = time.perf_counter()
    for _ in range(iterations):
        asyncio.run(orchestrator.run("test"))
    elapsed = (time.perf_counter() - start) * 1000.0
    avg_ms = elapsed / iterations
    print(f"Agent dispatch: avg {avg_ms:.4f} ms ({iterations} iterations)")
    print("Target: < 5 ms per call")
    print(f"Status: {'PASS' if avg_ms < 5.0 else 'FAIL'}")


if __name__ == "__main__":
    print("=== TOM Performance Benchmarks ===")
    print()
    benchmark_router_tier1()
    print()
    benchmark_router_tier2()
    print()
    benchmark_tool_dispatch()
    print()
    benchmark_agent_dispatch()
    print()
    print("=== Benchmarks Complete ===")
    print()
    print("Note: These are environment-dependent measurements.")
    print("Results vary based on hardware, system load, and Python runtime.")
