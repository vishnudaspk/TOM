"""Integration tests for Resource Governor with AgentOrchestrator and VisionManager.

Verifies:
- AgentOrchestrator acquires ModelRole.REASONING lease during LLM thinking
- AgentOrchestrator releases reasoning lease BEFORE tool dispatch (deadlock prevention)
- AgentOrchestrator streaming releases lease on completion and early cancellation
- VisionManager acquires ModelRole.VLM lease during Tier 3+ inference
- Nested Agent -> Tool -> VisionManager execution succeeds without deadlock
- Atomic admission rejection when concurrent requests exceed VRAM ceiling
- Negative resource estimates raise ValueError cleanly
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from tom.agents.base import Agent
from tom.agents.dependencies import AgentDependencies
from tom.agents.orchestrator import AgentOrchestrator
from tom.core.router import IntentRouter
from tom.models.base import LLMProvider
from tom.models.schemas import ModelRequest, ModelResponse, StreamChunk, ToolCallRequest
from tom.resources.manager import (
    InsufficientResourceError,
    MockTelemetryProvider,
    ModelRole,
    ResourceManager,
)
from tom.schemas.agent import AgentConfig
from tom.schemas.config import ResourcesConfig
from tom.schemas.router import IntentDomain, RoutingDecision
from tom.schemas.vision import (
    BoundingBox,
    CapturedFrame,
    VisionAnalysisRequest,
    VisionCapability,
    VLMResponse,
)
from tom.vision.manager import VisionManager
from tom.vision.vlm import VLMError, VLMProvider


class DummyLLMProvider(LLMProvider):
    def __init__(self, response: ModelResponse | None = None) -> None:
        self._response = response or ModelResponse(content="Thinking result", tool_calls=[])
        self.is_thinking = False

    async def check_health(self) -> bool:
        return True

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.is_thinking = True
        await asyncio.sleep(0.01)
        self.is_thinking = False
        return self._response

    async def stream(self, request: ModelRequest):
        self.is_thinking = True
        for token in ["Hello", " world", "!"]:
            await asyncio.sleep(0.01)
            yield StreamChunk(content=token)
        self.is_thinking = False


class SlowStreamingLLMProvider(LLMProvider):
    """Provider that simulates a slow streaming response for cancellation testing."""

    def __init__(self) -> None:
        self.is_thinking = False
        self.stream_started = asyncio.Event()
        self.chunks = ["Token1", " Token2", " Token3", " Token4", " Token5"]
        self.cancel_during_stream = False

    async def check_health(self) -> bool:
        return True

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.is_thinking = True
        await asyncio.sleep(0.01)
        self.is_thinking = False
        return ModelResponse(content="Done", tool_calls=[])

    async def stream(self, request: ModelRequest):
        self.is_thinking = True
        self.stream_started.set()
        try:
            for token in self.chunks:
                await asyncio.sleep(0.05)  # Slow streaming
                if self.cancel_during_stream:
                    raise asyncio.CancelledError()
                yield StreamChunk(content=token)
        finally:
            self.is_thinking = False


class DummyVLMProvider(VLMProvider):
    def __init__(self) -> None:
        self.is_analyzing = False

    async def analyze_image(self, request) -> VLMResponse:
        self.is_analyzing = True
        await asyncio.sleep(0.01)
        self.is_analyzing = False
        return VLMResponse(content="Visual scene description", bounding_boxes=[])

    async def locate_element(self, image, query: str) -> list[BoundingBox]:
        return [BoundingBox(left=10, top=10, right=50, bottom=50)]


@pytest.fixture
def resource_manager() -> ResourceManager:
    config = ResourcesConfig(max_vram_mb=7200, max_ram_mb=16384)
    telemetry = MockTelemetryProvider(
        vram_total_mb=8192.0,
        vram_free_mb=7200.0,
        gpu_available=True,
    )
    return ResourceManager(config=config, telemetry_provider=telemetry, safety_headroom_mb=500)


class TestGovernorOrchestratorIntegration:
    @pytest.mark.anyio
    async def test_orchestrator_acquires_and_releases_reasoning_lease(
        self, resource_manager: ResourceManager
    ) -> None:
        provider = DummyLLMProvider()
        agent = Agent(config=AgentConfig(), dependencies=AgentDependencies(model_provider=provider))
        router = MagicMock(spec=IntentRouter)
        router.classify = AsyncMock(
            return_value=RoutingDecision(
                domain=IntentDomain.CHAT,
                confidence=0.95,
                target_tool=None,
                route_tier=2,
                latency_ms=1.0,
            )
        )

        orchestrator = AgentOrchestrator(
            agent=agent,
            router=router,
            resource_manager=resource_manager,
        )

        assert resource_manager.allocated_vram_mb == 0
        assert not resource_manager.is_heavy_inference_active

        result = await orchestrator.run("What is the capital of France?")
        assert result == "Thinking result"

        # Lease must be cleanly released after run
        assert resource_manager.allocated_vram_mb == 0
        assert not resource_manager.is_heavy_inference_active
        assert resource_manager.active_leases_count == 0

    @pytest.mark.anyio
    async def test_orchestrator_releases_lease_before_tool_dispatch_no_deadlock(
        self, resource_manager: ResourceManager
    ) -> None:
        """Verify that when the agent model requests a tool call, the reasoning lease

        is released BEFORE execute_tool runs, allowing tools that need heavy locks
        (like VisionManager with VLM) to succeed without self-deadlock.
        """
        # Step 1: Model calls a vision tool
        tool_call = ToolCallRequest(
            id="call_1",
            name="dummy_tool",
            arguments={"query": "find button"},
        )
        step1_response = ModelResponse(
            content="Looking for button",
            tool_calls=[tool_call],
        )
        step2_response = ModelResponse(
            content="Button clicked successfully",
            tool_calls=[],
        )

        provider = MagicMock(spec=LLMProvider)
        provider.generate = AsyncMock(side_effect=[step1_response, step2_response])

        agent = Agent(config=AgentConfig(), dependencies=AgentDependencies(model_provider=provider))

        # Register a tool that acquires ModelRole.VLM
        tool_executed = asyncio.Event()

        async def mock_execute_tool(
            tool_name: str, params: dict, return_to_state=None, tool_call_id=None
        ):
            # Verify that _heavy_lock is NOT held by orchestrator during tool execution!
            assert not resource_manager.is_heavy_inference_active
            assert resource_manager.allocated_vram_mb == 0
            # Tool can acquire VLM lease cleanly without deadlock!
            async with resource_manager.acquire(ModelRole.VLM, required_vram_mb=5500):
                assert resource_manager.is_heavy_inference_active
                assert resource_manager.allocated_vram_mb == 5500
            tool_executed.set()
            return "tool result"

        agent.execute_tool = mock_execute_tool  # type: ignore

        router = MagicMock(spec=IntentRouter)
        router.classify = AsyncMock(
            return_value=RoutingDecision(
                domain=IntentDomain.CHAT,
                confidence=0.95,
                target_tool=None,
                route_tier=2,
                latency_ms=1.0,
            )
        )

        orchestrator = AgentOrchestrator(
            agent=agent,
            router=router,
            resource_manager=resource_manager,
        )

        result = await orchestrator.run("Find and click button")
        assert result == "Button clicked successfully"
        assert tool_executed.is_set()
        assert resource_manager.allocated_vram_mb == 0
        assert not resource_manager.is_heavy_inference_active

    @pytest.mark.anyio
    async def test_streaming_early_exit_releases_lease(
        self, resource_manager: ResourceManager
    ) -> None:
        provider = DummyLLMProvider()
        agent = Agent(config=AgentConfig(), dependencies=AgentDependencies(model_provider=provider))
        router = MagicMock(spec=IntentRouter)
        router.classify = AsyncMock(
            return_value=RoutingDecision(
                domain=IntentDomain.CHAT,
                confidence=0.95,
                target_tool=None,
                route_tier=2,
                latency_ms=1.0,
            )
        )

        orchestrator = AgentOrchestrator(
            agent=agent,
            router=router,
            resource_manager=resource_manager,
        )

        # Consume only 1 token and break
        stream = orchestrator.stream("Stream test")
        try:
            async for token in stream:
                assert token == "Hello"
                break
        finally:
            await stream.aclose()

        # Context manager exit must clean up the lease immediately
        assert resource_manager.allocated_vram_mb == 0
        assert not resource_manager.is_heavy_inference_active


class TestGovernorVisionIntegration:
    @pytest.mark.anyio
    async def test_vision_manager_tier3_acquires_vlm_lease(
        self, resource_manager: ResourceManager
    ) -> None:
        vlm = DummyVLMProvider()
        vision = VisionManager(
            vlm_provider=vlm,
            resource_manager=resource_manager,
        )

        req = VisionAnalysisRequest(
            query="Describe this UI layout",
            capability=VisionCapability.FAST_VLM,
        )

        # Mock capture
        vision.capture_service.capture_screen = MagicMock(
            return_value=CapturedFrame(width=100, height=100, data=b"fake")
        )
        vision._resolve_image = MagicMock()  # type: ignore

        res = await vision.analyze(req)
        assert res.text == "Visual scene description"
        assert resource_manager.allocated_vram_mb == 0
        assert not resource_manager.is_heavy_inference_active

    @pytest.mark.anyio
    async def test_vision_manager_insufficient_vram_raises_vlm_error(self) -> None:
        config = ResourcesConfig(max_vram_mb=7200)
        # Mock telemetry reporting only 500 MB free (equal to headroom -> 0 usable)
        telemetry = MockTelemetryProvider(
            vram_total_mb=8192.0,
            vram_free_mb=500.0,
            gpu_available=True,
        )
        mgr = ResourceManager(config=config, telemetry_provider=telemetry, safety_headroom_mb=500)

        vlm = DummyVLMProvider()
        vision = VisionManager(
            vlm_provider=vlm,
            resource_manager=mgr,
        )

        req = VisionAnalysisRequest(
            query="Describe layout",
            capability=VisionCapability.FAST_VLM,
        )
        vision._resolve_image = MagicMock()  # type: ignore

        with pytest.raises(VLMError) as exc_info:
            await vision.analyze(req)
        assert "VRAM pressure detected" in str(exc_info.value)
        assert mgr.allocated_vram_mb == 0


class TestGovernorAtomicAdmissionAndValidation:
    @pytest.mark.anyio
    async def test_negative_resource_estimates_raise_value_error(
        self, resource_manager: ResourceManager
    ) -> None:
        with pytest.raises(ValueError):
            resource_manager.check_budget(required_vram_mb=-100)

        with pytest.raises(ValueError):
            resource_manager.check_budget(required_ram_mb=-50)

        with pytest.raises(ValueError):
            await resource_manager.acquire(ModelRole.ROUTER, required_vram_mb=-10)

    @pytest.mark.anyio
    async def test_concurrent_admissions_do_not_overadmit_ceiling(
        self, resource_manager: ResourceManager
    ) -> None:
        # Usable ceiling is 7200 - 500 = 6700 MB.
        # Two concurrent requests for 5000 MB cannot both succeed.
        results: list[str] = []

        async def try_acquire(name: str) -> None:
            try:
                async with resource_manager.acquire(ModelRole.ROUTER, required_vram_mb=5000):
                    results.append(f"{name}_admitted")
                    await asyncio.sleep(0.02)
            except InsufficientResourceError:
                results.append(f"{name}_rejected")

        await asyncio.gather(try_acquire("req1"), try_acquire("req2"))

        assert "req1_admitted" in results or "req2_admitted" in results
        assert "req1_rejected" in results or "req2_rejected" in results
        assert len(results) == 2
        assert resource_manager.allocated_vram_mb == 0


class TestGovernorCancellationAndCleanup:
    """Tests for cancellation, cleanup, and resource accounting edge cases."""

    @pytest.mark.anyio
    async def test_streaming_cancellation_releases_lease(
        self, resource_manager: ResourceManager
    ) -> None:
        """When streaming is cancelled mid-request, the lease must be released."""
        provider = SlowStreamingLLMProvider()
        agent = Agent(config=AgentConfig(), dependencies=AgentDependencies(model_provider=provider))
        router = MagicMock(spec=IntentRouter)
        router.classify = AsyncMock(
            return_value=RoutingDecision(
                domain=IntentDomain.CHAT,
                confidence=0.95,
                target_tool=None,
                route_tier=2,
                latency_ms=1.0,
            )
        )

        orchestrator = AgentOrchestrator(
            agent=agent,
            router=router,
            resource_manager=resource_manager,
        )

        # Start streaming and cancel after first token
        stream = orchestrator.stream("Stream test")
        try:
            async for _ in stream:
                # Give the stream time to start
                await provider.stream_started.wait()
                await asyncio.sleep(0.02)
                break
        finally:
            await stream.aclose()

        # Lease must be released even though provider was mid-stream
        assert resource_manager.allocated_vram_mb == 0
        assert not resource_manager.is_heavy_inference_active

    @pytest.mark.anyio
    async def test_streaming_provider_exception_releases_lease(
        self, resource_manager: ResourceManager
    ) -> None:
        """When provider raises an exception during streaming, the lease must be released."""

        class FailingStreamProvider(LLMProvider):
            async def check_health(self) -> bool:
                return True

            async def generate(self, request: ModelRequest) -> ModelResponse:
                return ModelResponse(content="Done", tool_calls=[])

            async def stream(self, request: ModelRequest):
                yield StreamChunk(content="Start")
                await asyncio.sleep(0.01)
                raise RuntimeError("Provider streaming error")

        provider = FailingStreamProvider()
        agent = Agent(config=AgentConfig(), dependencies=AgentDependencies(model_provider=provider))
        router = MagicMock(spec=IntentRouter)
        router.classify = AsyncMock(
            return_value=RoutingDecision(
                domain=IntentDomain.CHAT,
                confidence=0.95,
                target_tool=None,
                route_tier=2,
                latency_ms=1.0,
            )
        )

        orchestrator = AgentOrchestrator(
            agent=agent,
            router=router,
            resource_manager=resource_manager,
        )

        # Stream should raise the error and release lease
        stream = orchestrator.stream("Stream test")
        try:
            async for _ in stream:
                pass
        except RuntimeError:
            pass  # Expected

        # Lease must be released despite provider error
        assert resource_manager.allocated_vram_mb == 0
        assert not resource_manager.is_heavy_inference_active

    @pytest.mark.anyio
    async def test_cancellation_during_lease_wait_releases_lock(
        self, resource_manager: ResourceManager
    ) -> None:
        """Cancelling while waiting for heavy lock must not leak the lock."""
        # Hold the heavy lock with a reasoning lease
        first_lease = await resource_manager.acquire(ModelRole.REASONING, required_vram_mb=2000)
        assert resource_manager.is_heavy_inference_active

        # Try to acquire VLM lease (will wait on heavy lock)
        wait_started = asyncio.Event()

        async def waiting_acquirer() -> None:
            wait_started.set()
            async with resource_manager.acquire(ModelRole.VLM, required_vram_mb=2000):
                pass

        task = asyncio.create_task(waiting_acquirer())
        await wait_started.wait()
        await asyncio.sleep(0.01)

        # Cancel while waiting on the lock
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Release first lease - lock should be free
        await first_lease.release()
        assert not resource_manager.is_heavy_inference_active
        assert resource_manager.allocated_vram_mb == 0

    @pytest.mark.anyio
    async def test_repeated_release_is_idempotent(self, resource_manager: ResourceManager) -> None:
        """Multiple release calls must not cause negative accounting."""
        lease = await resource_manager.acquire(ModelRole.REASONING, required_vram_mb=3000)
        assert resource_manager.allocated_vram_mb == 3000

        await lease.release()
        assert resource_manager.allocated_vram_mb == 0

        # Repeated release must be idempotent
        await lease.release()
        await resource_manager.release(lease)
        assert resource_manager.allocated_vram_mb == 0
        assert not resource_manager.is_heavy_inference_active

    @pytest.mark.anyio
    async def test_zero_accounting_drift_across_operations(
        self, resource_manager: ResourceManager
    ) -> None:
        """After many acquire/release cycles, accounting must return to zero."""
        for _ in range(20):
            async with resource_manager.acquire(ModelRole.ROUTER, required_vram_mb=1000):
                pass
            async with resource_manager.acquire(ModelRole.VLM, required_vram_mb=1500):
                pass
            async with resource_manager.acquire(ModelRole.REASONING, required_vram_mb=2000):
                pass

        assert resource_manager.allocated_vram_mb == 0
        assert resource_manager.allocated_ram_mb == 0
        assert resource_manager.active_leases_count == 0
        assert not resource_manager.is_heavy_inference_active

    @pytest.mark.anyio
    async def test_vlm_cancellation_follows_same_policy(
        self, resource_manager: ResourceManager
    ) -> None:
        """VLM inference cancellation must release lease like reasoning."""

        class SlowVLMProvider(VLMProvider):
            def __init__(self) -> None:
                self.started = asyncio.Event()

            async def analyze_image(self, request) -> VLMResponse:
                self.started.set()
                await asyncio.sleep(0.1)
                return VLMResponse(content="Done", bounding_boxes=[])

            async def locate_element(self, image, query: str) -> list[BoundingBox]:
                self.started.set()
                await asyncio.sleep(0.1)
                return [BoundingBox(left=10, top=10, right=50, bottom=50)]

        vlm = SlowVLMProvider()
        vision = VisionManager(
            vlm_provider=vlm,
            resource_manager=resource_manager,
        )

        req = VisionAnalysisRequest(
            query="Describe this UI layout",
            capability=VisionCapability.FAST_VLM,
        )
        vision.capture_service.capture_screen = MagicMock(
            return_value=CapturedFrame(width=100, height=100, data=b"fake")
        )
        vision._resolve_image = MagicMock()  # type: ignore

        # Start analysis and cancel
        task = asyncio.create_task(vision.analyze(req))
        await vlm.started.wait()
        await asyncio.sleep(0.01)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        # Lease must be released
        assert resource_manager.allocated_vram_mb == 0
        assert not resource_manager.is_heavy_inference_active

    @pytest.mark.anyio
    async def test_normal_streaming_completes_releases_lease_before_tools(
        self, resource_manager: ResourceManager
    ) -> None:
        """Normal streaming completion must release lease before tool dispatch."""
        # Model response with tool call on second step
        tool_call = ToolCallRequest(
            id="call_1",
            name="vision_tool",
            arguments={"query": "find button"},
        )
        step2_response = ModelResponse(
            content="Button found",
            tool_calls=[],
        )

        # Create a proper streaming provider mock
        class StreamingMockProvider(LLMProvider):
            def __init__(self) -> None:
                self.stream_chunks = [
                    StreamChunk(content="Looking"),
                    StreamChunk(content=" for"),
                    StreamChunk(content=" button", tool_call_delta=tool_call),
                ]

            async def check_health(self) -> bool:
                return True

            async def generate(self, request: ModelRequest) -> ModelResponse:
                return step2_response

            async def stream(self, request: ModelRequest):
                for chunk in self.stream_chunks:
                    yield chunk

        provider = StreamingMockProvider()
        agent = Agent(config=AgentConfig(), dependencies=AgentDependencies(model_provider=provider))

        tool_executed = asyncio.Event()

        async def mock_execute_tool(
            tool_name: str, params: dict, return_to_state=None, tool_call_id=None
        ):
            # Verify lease is released before tool runs
            assert not resource_manager.is_heavy_inference_active
            assert resource_manager.allocated_vram_mb == 0
            tool_executed.set()
            return "tool result"

        agent.execute_tool = mock_execute_tool  # type: ignore

        router = MagicMock(spec=IntentRouter)
        router.classify = AsyncMock(
            return_value=RoutingDecision(
                domain=IntentDomain.CHAT,
                confidence=0.95,
                target_tool=None,
                route_tier=2,
                latency_ms=1.0,
            )
        )

        orchestrator = AgentOrchestrator(
            agent=agent,
            router=router,
            resource_manager=resource_manager,
        )

        # Use stream() which internally uses _stream_agent_loop
        stream = orchestrator.stream("Find button")
        result = ""
        try:
            async for token in stream:
                result += token
        finally:
            await stream.aclose()

        # Tool should have been executed
        assert tool_executed.is_set()
        # Lease should be released
        assert resource_manager.allocated_vram_mb == 0
        assert not resource_manager.is_heavy_inference_active

    @pytest.mark.anyio
    async def test_consumer_cancellation_retains_heavy_lease_until_provider_terminates(
        self, resource_manager: ResourceManager
    ) -> None:
        """Verify that consumer cancellation does NOT prematurely release heavy capacity.

        The heavy lease MUST remain held while the underlying provider stream is closing,
        blocking any second heavy inference until termination is confirmed.
        """
        provider_aclose_started = asyncio.Event()
        provider_can_finish_close = asyncio.Event()
        provider_closed = asyncio.Event()

        class BarrierStreamingProvider(LLMProvider):
            def __init__(self) -> None:
                self.started = asyncio.Event()

            async def check_health(self) -> bool:
                return True

            async def generate(self, request: ModelRequest) -> ModelResponse:
                return ModelResponse(content="Done", tool_calls=[])

            async def stream(self, request: ModelRequest):
                self.started.set()
                try:
                    yield StreamChunk(content="chunk1")
                    await asyncio.sleep(1.0)
                    yield StreamChunk(content="chunk2")
                finally:
                    provider_aclose_started.set()
                    await provider_can_finish_close.wait()
                    provider_closed.set()

        provider = BarrierStreamingProvider()
        agent = Agent(config=AgentConfig(), dependencies=AgentDependencies(model_provider=provider))
        router = MagicMock(spec=IntentRouter)
        router.classify = AsyncMock(
            return_value=RoutingDecision(
                domain=IntentDomain.CHAT,
                confidence=0.95,
                target_tool=None,
                route_tier=2,
                latency_ms=1.0,
            )
        )

        orchestrator = AgentOrchestrator(
            agent=agent,
            router=router,
            resource_manager=resource_manager,
        )

        stream = orchestrator.stream("Barrier test")

        # Step 1: Read the first token
        first_token = await stream.__anext__()
        assert first_token == "chunk1"
        assert resource_manager.is_heavy_inference_active

        # Step 2: Consumer closes the stream asynchronously (simulating early consumer exit / cancel)
        close_task = asyncio.create_task(stream.aclose())

        # Step 3: Wait until provider enters its cleanup (aclose)
        await provider_aclose_started.wait()

        # At this point, the consumer has requested close, but provider has NOT finished!
        assert not provider_closed.is_set()
        # CRITICAL SAFETY INVARIANT: The heavy lease MUST STILL BE ACTIVE!
        assert resource_manager.is_heavy_inference_active
        assert resource_manager.allocated_vram_mb == 4531

        # Step 4: Attempting a second heavy inference (VLM) must be blocked because the slot is retained
        second_heavy_blocked = asyncio.Event()
        second_heavy_admitted = asyncio.Event()

        async def second_heavy_op():
            second_heavy_blocked.set()
            async with resource_manager.acquire(ModelRole.VLM, required_vram_mb=5500):
                second_heavy_admitted.set()

        second_task = asyncio.create_task(second_heavy_op())
        await second_heavy_blocked.wait()
        await asyncio.sleep(0.02)

        # Confirm the second heavy op has NOT been admitted
        assert not second_heavy_admitted.is_set()

        # Step 5: Now allow the provider to finish its termination
        provider_can_finish_close.set()
        await close_task
        assert provider_closed.is_set()

        # Step 6: With the first provider confirmed terminated, second heavy op is now admitted
        await asyncio.wait_for(second_heavy_admitted.wait(), timeout=1.0)
        await second_task

        # Everything is clean now
        assert resource_manager.allocated_vram_mb == 0
        assert not resource_manager.is_heavy_inference_active

    @pytest.mark.anyio
    async def test_reasoning_and_vlm_mutual_exclusion(
        self, resource_manager: ResourceManager
    ) -> None:
        """Verify strict mutual exclusion between Reasoning and VLM."""
        order: list[str] = []
        reasoning_running = asyncio.Event()
        reasoning_can_finish = asyncio.Event()

        async def run_reasoning():
            async with resource_manager.acquire(ModelRole.REASONING, required_vram_mb=5200):
                order.append("reasoning_start")
                reasoning_running.set()
                await reasoning_can_finish.wait()
                order.append("reasoning_end")

        async def run_vlm():
            await reasoning_running.wait()
            # Attempt to acquire while reasoning is active
            async with resource_manager.acquire(ModelRole.VLM, required_vram_mb=5500):
                order.append("vlm_run")

        task1 = asyncio.create_task(run_reasoning())
        task2 = asyncio.create_task(run_vlm())

        await reasoning_running.wait()
        await asyncio.sleep(0.02)
        # VLM must not have run yet
        assert order == ["reasoning_start"]

        # Let reasoning finish
        reasoning_can_finish.set()
        await asyncio.gather(task1, task2)

        assert order == ["reasoning_start", "reasoning_end", "vlm_run"]
        assert resource_manager.allocated_vram_mb == 0
        assert not resource_manager.is_heavy_inference_active
