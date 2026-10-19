"""Adversarial concurrency, timeout, cancellation, starvation, and deadlock stress tests.

Phase 12 — Iteration 12.4 Concurrency Stress Suite.
Tests:
- Section A: Admission and mutual exclusion under adversarial contention
- Section B: Priority, fairness, and starvation aging with injected clock
- Section C: Cancellation, timeout, and cleanup at grant/reservation boundaries
- Section D: Deadlock and lock-order verification (agent -> tool -> vision)
- Section E: 100+ repeated deterministic concurrency cycles with strict invariant assertions
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from tom.resources.manager import (
    InsufficientResourceError,
    MockTelemetryProvider,
    ModelRole,
    ResourceLockError,
    ResourceManager,
)
from tom.resources.policies import TaskPriority
from tom.schemas.config import ResourcesConfig


class FakeClock:
    """Injected deterministic clock for starvation aging tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def time(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def resource_manager() -> ResourceManager:
    config = ResourcesConfig(max_vram_mb=7200, max_ram_mb=16384)
    telemetry = MockTelemetryProvider(
        vram_total_mb=8192.0,
        vram_free_mb=8000.0,
        gpu_available=True,
    )
    return ResourceManager(config=config, telemetry_provider=telemetry, safety_headroom_mb=500)


# ============================================================================
# Section A: Admission and Mutual Exclusion
# ============================================================================


class TestAdmissionAndMutualExclusion:
    """Test that concurrent reasoning and VLM requests never violate mutual exclusion or accounting."""

    @pytest.mark.anyio
    async def test_concurrent_reasoning_and_vlm_never_hold_leases_simultaneously(
        self, resource_manager: ResourceManager
    ) -> None:
        """Concurrent reasoning and VLM requests never hold heavy leases simultaneously."""
        concurrency = 8
        active_heavy_roles: list[ModelRole] = []
        violations: list[str] = []

        async def worker(role: ModelRole) -> None:
            async with resource_manager.acquire(role, required_vram_mb=3000):
                # Verify invariant inside lease
                if role in resource_manager.HEAVY_ROLES:
                    active_heavy_roles.append(role)
                    if len(active_heavy_roles) > 1:
                        violations.append(
                            f"Multiple heavy leases held: {[str(r) for r in active_heavy_roles]}"
                        )
                    # Yield slightly to allow contention
                    await asyncio.sleep(0.005)
                    active_heavy_roles.remove(role)

        tasks = []
        for i in range(concurrency):
            role = ModelRole.REASONING if i % 2 == 0 else ModelRole.VLM
            tasks.append(asyncio.create_task(worker(role)))

        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

        assert violations == []
        assert active_heavy_roles == []
        assert resource_manager.allocated_vram_mb == 0
        assert resource_manager.active_leases_count == 0
        assert not resource_manager.is_heavy_inference_active

    @pytest.mark.anyio
    async def test_concurrent_acquisition_and_release_never_double_grant(
        self, resource_manager: ResourceManager
    ) -> None:
        """Concurrent acquisition and release never double-grant capacity."""
        grants_held = 0
        max_simultaneous_grants = 0
        lock = asyncio.Lock()
        tasks = []

        async def rapid_worker() -> None:
            nonlocal grants_held, max_simultaneous_grants
            for _ in range(5):
                async with resource_manager.acquire(ModelRole.REASONING, required_vram_mb=3000):
                    async with lock:
                        grants_held += 1
                        if grants_held > max_simultaneous_grants:
                            max_simultaneous_grants = grants_held
                    await asyncio.sleep(0.002)
                    async with lock:
                        grants_held -= 1

        for _ in range(6):
            tasks.append(asyncio.create_task(rapid_worker()))

        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

        # Heavy role must have at most 1 simultaneous grant
        assert max_simultaneous_grants == 1
        assert grants_held == 0
        assert resource_manager.allocated_vram_mb == 0

    @pytest.mark.anyio
    async def test_vram_and_ram_accounting_within_software_reservation_limits(
        self, resource_manager: ResourceManager
    ) -> None:
        """VRAM/RAM accounting strictly remains within configured reservation limits."""
        usable_vram = resource_manager.config.max_vram_mb - resource_manager.safety_headroom_mb
        peak_vram = 0
        peak_ram = 0
        accounting_lock = asyncio.Lock()
        tasks = []

        async def memory_consumer(vram: int, ram: int) -> None:
            nonlocal peak_vram, peak_ram
            try:
                async with resource_manager.acquire(
                    ModelRole.GENERAL, required_vram_mb=vram, required_ram_mb=ram
                ):
                    async with accounting_lock:
                        if resource_manager.allocated_vram_mb > peak_vram:
                            peak_vram = resource_manager.allocated_vram_mb
                        if resource_manager.allocated_ram_mb > peak_ram:
                            peak_ram = resource_manager.allocated_ram_mb
                    await asyncio.sleep(0.005)
            except InsufficientResourceError:
                pass  # Safely rejected if budget exceeded

        # Launch requests that collectively would exceed budget if not checked
        for _ in range(10):
            tasks.append(asyncio.create_task(memory_consumer(1000, 2000)))

        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

        assert peak_vram <= usable_vram
        assert peak_ram <= resource_manager.config.max_ram_mb
        assert resource_manager.allocated_vram_mb == 0
        assert resource_manager.allocated_ram_mb == 0

    @pytest.mark.anyio
    async def test_different_reservation_sizes_evaluated_correctly_at_admission(
        self, resource_manager: ResourceManager
    ) -> None:
        """Different reservation sizes are evaluated correctly at admission time."""
        usable_vram = resource_manager.config.max_vram_mb - resource_manager.safety_headroom_mb

        # Exactly at limit -> succeeds
        async with resource_manager.acquire(
            ModelRole.GENERAL, required_vram_mb=usable_vram, required_ram_mb=1000
        ) as lease:
            assert lease.vram_mb == usable_vram
            assert resource_manager.allocated_vram_mb == usable_vram

            # Any additional VRAM must fail
            with pytest.raises(InsufficientResourceError):
                await resource_manager.acquire(
                    ModelRole.GENERAL, required_vram_mb=1, blocking=False
                )

        assert resource_manager.allocated_vram_mb == 0

        # Over the limit -> fails immediately
        with pytest.raises(InsufficientResourceError):
            await resource_manager.acquire(
                ModelRole.GENERAL, required_vram_mb=usable_vram + 1, blocking=False
            )

    @pytest.mark.anyio
    async def test_high_priority_waiters_cannot_bypass_admission_checks(
        self, resource_manager: ResourceManager
    ) -> None:
        """High-priority waiters cannot bypass admission checks or in-flight lease safety."""
        usable_vram = resource_manager.config.max_vram_mb - resource_manager.safety_headroom_mb

        # Fill capacity with interactive lease
        async with resource_manager.acquire(
            ModelRole.GENERAL, required_vram_mb=usable_vram, priority=TaskPriority.USER_INTERACTIVE
        ):
            # VOICE_INTERACTIVE (priority 0) request that exceeds budget must still fail
            with pytest.raises(InsufficientResourceError):
                await resource_manager.acquire(
                    ModelRole.GENERAL,
                    required_vram_mb=100,
                    priority=TaskPriority.VOICE_INTERACTIVE,
                    blocking=False,
                )


# ============================================================================
# Section B: Priority, Fairness, and Starvation Aging
# ============================================================================


class TestPriorityAndFairness:
    """Test priority arbitration, FIFO sequence order, and starvation aging."""

    @pytest.mark.anyio
    async def test_interactive_outranks_queued_background_on_capacity_free(
        self, resource_manager: ResourceManager
    ) -> None:
        """Interactive requests outrank queued background requests when capacity becomes free."""
        admission_order: list[str] = []
        holder_started = asyncio.Event()
        holder_release = asyncio.Event()

        async def initial_holder() -> None:
            async with resource_manager.acquire(
                ModelRole.REASONING, required_vram_mb=3000, priority=TaskPriority.USER_INTERACTIVE
            ):
                holder_started.set()
                await holder_release.wait()

        async def background_waiter() -> None:
            await holder_started.wait()
            async with resource_manager.acquire(
                ModelRole.REASONING, required_vram_mb=3000, priority=TaskPriority.BACKGROUND
            ):
                admission_order.append("background")

        async def interactive_waiter() -> None:
            await holder_started.wait()
            # Give background waiter a moment to queue first
            await asyncio.sleep(0.01)
            async with resource_manager.acquire(
                ModelRole.REASONING, required_vram_mb=3000, priority=TaskPriority.USER_INTERACTIVE
            ):
                admission_order.append("interactive")

        t_holder = asyncio.create_task(initial_holder())
        t_bg = asyncio.create_task(background_waiter())
        t_ui = asyncio.create_task(interactive_waiter())

        await holder_started.wait()
        await asyncio.sleep(0.02)  # Ensure both waiters are queued

        # Release initial holder
        holder_release.set()

        try:
            await asyncio.gather(t_holder, t_bg, t_ui)
        finally:
            for t in [t_holder, t_bg, t_ui]:
                if not t.done():
                    t.cancel()

        # Interactive must be admitted before background
        assert admission_order == ["interactive", "background"]

    @pytest.mark.anyio
    async def test_equal_priority_waiters_retain_fifo_ordering(
        self, resource_manager: ResourceManager
    ) -> None:
        """Equal-priority waiters retain strict FIFO ordering."""
        admission_order: list[int] = []
        holder_started = asyncio.Event()
        holder_release = asyncio.Event()

        async def initial_holder() -> None:
            async with resource_manager.acquire(ModelRole.REASONING, required_vram_mb=3000):
                holder_started.set()
                await holder_release.wait()

        async def waiter(idx: int) -> None:
            await holder_started.wait()
            async with resource_manager.acquire(
                ModelRole.REASONING, required_vram_mb=3000, priority=TaskPriority.USER_INTERACTIVE
            ):
                admission_order.append(idx)

        t_holder = asyncio.create_task(initial_holder())
        await holder_started.wait()

        waiter_tasks = []
        for i in range(4):
            waiter_tasks.append(asyncio.create_task(waiter(i)))
            await asyncio.sleep(0.005)  # Enqueue in strict sequential order

        holder_release.set()

        try:
            await asyncio.gather(t_holder, *waiter_tasks)
        finally:
            for t in [t_holder, *waiter_tasks]:
                if not t.done():
                    t.cancel()

        assert admission_order == [0, 1, 2, 3]

    @pytest.mark.anyio
    async def test_starvation_aging_affects_real_admission_under_injected_clock(self) -> None:
        """Starvation aging promotes a waiting background request over a newer higher-priority request."""
        fake_clock = FakeClock(start=1000.0)
        config = ResourcesConfig(max_vram_mb=7200, max_ram_mb=16384)
        telemetry = MockTelemetryProvider(
            vram_total_mb=8192.0, vram_free_mb=7192.0, gpu_available=True
        )
        rm = ResourceManager(
            config=config,
            telemetry_provider=telemetry,
            safety_headroom_mb=500,
            clock=fake_clock.time,
        )

        admission_order: list[str] = []
        holder_started = asyncio.Event()
        holder_release = asyncio.Event()

        async def initial_holder() -> None:
            async with rm.acquire(ModelRole.REASONING, required_vram_mb=3000):
                holder_started.set()
                await holder_release.wait()

        async def background_waiter() -> None:
            await holder_started.wait()
            async with rm.acquire(
                ModelRole.REASONING, required_vram_mb=3000, priority=TaskPriority.BACKGROUND
            ):
                admission_order.append("background")

        async def routing_waiter() -> None:
            await holder_started.wait()
            async with rm.acquire(
                ModelRole.REASONING, required_vram_mb=3000, priority=TaskPriority.ROUTING
            ):
                admission_order.append("routing")

        t_holder = asyncio.create_task(initial_holder())
        await holder_started.wait()

        # Enqueue background waiter at t=1000
        t_bg = asyncio.create_task(background_waiter())
        await asyncio.sleep(0.01)

        # Advance clock by 45 seconds (3 aging thresholds: 5 - 3 = effective priority 2)
        # BACKGROUND (5) -> ROUTING (2)
        fake_clock.advance(46.0)

        # Now enqueue routing waiter at t=1046 (priority 2, but later sequence)
        t_routing = asyncio.create_task(routing_waiter())
        await asyncio.sleep(0.01)

        # Release holder
        holder_release.set()

        try:
            await asyncio.gather(t_holder, t_bg, t_routing)
        finally:
            for t in [t_holder, t_bg, t_routing]:
                if not t.done():
                    t.cancel()

        # Aged background request has effective priority 2 and earlier sequence than routing!
        assert admission_order[0] == "background"
        assert admission_order[1] == "routing"

    @pytest.mark.anyio
    async def test_proactive_tasks_propagate_background_priority(
        self, resource_manager: ResourceManager
    ) -> None:
        """Verify TaskPriority.BACKGROUND is propagated through proactive task execution."""
        from tom.agents.proactive import PROACTIVE_RESOURCE_PRIORITY

        assert PROACTIVE_RESOURCE_PRIORITY == TaskPriority.BACKGROUND

        # Direct verification that acquire accepts and honors this constant
        async with resource_manager.acquire(
            ModelRole.GENERAL, required_vram_mb=500, priority=PROACTIVE_RESOURCE_PRIORITY
        ) as lease:
            assert lease.vram_mb == 500


# ============================================================================
# Section C: Cancellation, Timeout, and Cleanup
# ============================================================================


class TestCancellationTimeoutAndCleanup:
    """Test cancellation safety, timeouts, and leak prevention at boundaries."""

    @pytest.mark.anyio
    async def test_cancellation_while_waiting_leaves_no_orphaned_waiter_or_reservation(
        self, resource_manager: ResourceManager
    ) -> None:
        """Cancellation while waiting leaves no orphaned waiter or reservation."""
        holder_started = asyncio.Event()
        holder_release = asyncio.Event()

        async def holder() -> None:
            async with resource_manager.acquire(ModelRole.REASONING, required_vram_mb=3000):
                holder_started.set()
                await holder_release.wait()

        t_holder = asyncio.create_task(holder())
        await holder_started.wait()

        # Start waiter that will be cancelled
        waiter_queued = asyncio.Event()

        async def waiter() -> None:
            waiter_queued.set()
            async with resource_manager.acquire(ModelRole.REASONING, required_vram_mb=3000):
                pass

        t_waiter = asyncio.create_task(waiter())
        await waiter_queued.wait()
        await asyncio.sleep(0.01)

        # Cancel the waiter before it is granted
        t_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t_waiter

        # Waiters queue should be empty of the cancelled waiter
        assert await resource_manager._priority_queue.get_waiters_count() == 0

        # Release holder and verify clean state
        holder_release.set()
        await t_holder

        assert resource_manager.allocated_vram_mb == 0
        assert resource_manager.active_leases_count == 0
        assert not resource_manager.is_heavy_inference_active

    @pytest.mark.anyio
    async def test_timeout_removes_waiter_safely_and_allows_later_requests(
        self, resource_manager: ResourceManager
    ) -> None:
        """Timeout removes the waiter safely and allows later requests to progress."""
        holder_started = asyncio.Event()
        holder_release = asyncio.Event()

        async def holder() -> None:
            async with resource_manager.acquire(ModelRole.REASONING, required_vram_mb=3000):
                holder_started.set()
                await holder_release.wait()

        t_holder = asyncio.create_task(holder())
        await holder_started.wait()

        # Waiter with a short timeout
        with pytest.raises(ResourceLockError) as exc_info:
            await resource_manager.acquire(ModelRole.REASONING, required_vram_mb=3000, timeout=0.02)
        assert "Timeout of 0.02s exceeded" in str(exc_info.value)

        # Ensure waiter was cleanly removed from queue
        assert await resource_manager._priority_queue.get_waiters_count() == 0

        # Release holder
        holder_release.set()
        await t_holder

        # Later request can acquire immediately
        async with resource_manager.acquire(ModelRole.REASONING, required_vram_mb=3000) as lease:
            assert lease.vram_mb == 3000

        assert resource_manager.allocated_vram_mb == 0

    @pytest.mark.anyio
    async def test_cancellation_at_grant_reservation_boundary_does_not_leak(
        self, resource_manager: ResourceManager
    ) -> None:
        """Cancellation right as a waiter is granted does not leak or double-release."""
        holder_started = asyncio.Event()
        holder_release = asyncio.Event()

        async def holder() -> None:
            async with resource_manager.acquire(ModelRole.REASONING, required_vram_mb=3000):
                holder_started.set()
                await holder_release.wait()

        t_holder = asyncio.create_task(holder())
        await holder_started.wait()

        async def waiter() -> None:
            async with resource_manager.acquire(ModelRole.REASONING, required_vram_mb=3000):
                pass

        waiter_task = asyncio.create_task(waiter())
        await asyncio.sleep(0.01)

        # Release holder and simultaneously cancel waiter to race at boundary
        holder_release.set()
        waiter_task.cancel()

        try:
            await waiter_task
        except asyncio.CancelledError:
            pass

        await t_holder

        # Accounting must be completely clean
        assert resource_manager.allocated_vram_mb == 0
        assert resource_manager.active_leases_count == 0
        assert not resource_manager.is_heavy_inference_active

    @pytest.mark.anyio
    async def test_repeated_release_remains_idempotent(
        self, resource_manager: ResourceManager
    ) -> None:
        """Repeated release remains idempotent and does not corrupt counters."""
        lease = await resource_manager.acquire(ModelRole.GENERAL, required_vram_mb=1000)
        assert resource_manager.allocated_vram_mb == 1000

        # First release
        await lease.release()
        assert resource_manager.allocated_vram_mb == 0

        # Second release
        await lease.release()
        assert resource_manager.allocated_vram_mb == 0

        # Third release via resource_manager directly
        await resource_manager.release(lease)
        assert resource_manager.allocated_vram_mb == 0

    @pytest.mark.anyio
    async def test_provider_exceptions_and_cleanup_preserve_accounting(
        self, resource_manager: ResourceManager
    ) -> None:
        """Exceptions inside lease context manager preserve consistent accounting."""
        with pytest.raises(RuntimeError):
            async with resource_manager.acquire(ModelRole.REASONING, required_vram_mb=3000):
                raise RuntimeError("Simulated inference engine error")

        assert resource_manager.allocated_vram_mb == 0
        assert resource_manager.active_leases_count == 0
        assert not resource_manager.is_heavy_inference_active

    @pytest.mark.anyio
    async def test_cancelled_consumer_retains_lease_until_provider_completes(
        self, resource_manager: ResourceManager
    ) -> None:
        """A cancelled consumer does not make capacity available while inference is still cleaning up."""
        provider_cleanup_done = asyncio.Event()
        release_provider = asyncio.Event()

        async def fake_streaming_inference() -> AsyncIterator[str]:
            async with resource_manager.acquire(ModelRole.REASONING, required_vram_mb=4000):
                try:
                    yield "token_1"
                    yield "token_2"
                finally:
                    # Simulate provider cleanup (e.g. waiting for aclose)
                    await release_provider.wait()
                    provider_cleanup_done.set()

        gen = fake_streaming_inference()
        first_token = await gen.asend(None)
        assert first_token == "token_1"

        # Consumer decides to cancel / aclose
        aclose_task = asyncio.create_task(gen.aclose())
        await asyncio.sleep(0.01)

        # Capacity must STILL be held because provider has not completed cleanup!
        assert resource_manager.allocated_vram_mb == 4000
        assert resource_manager.is_heavy_inference_active

        # Another heavy request cannot acquire yet
        with pytest.raises(ResourceLockError):
            await resource_manager.acquire(ModelRole.VLM, required_vram_mb=3000, blocking=False)

        # Now provider cleanup completes
        release_provider.set()
        await aclose_task
        assert provider_cleanup_done.is_set()

        # Capacity is restored only after completion
        assert resource_manager.allocated_vram_mb == 0
        assert not resource_manager.is_heavy_inference_active


# ============================================================================
# Section D: Deadlock and Lock-Order Verification
# ============================================================================


class TestDeadlockAndLockOrder:
    """Verify lock ordering, nested execution safety, and lack of permanent locks."""

    @pytest.mark.anyio
    async def test_reasoning_lease_released_before_tool_dispatch(
        self, resource_manager: ResourceManager
    ) -> None:
        """Reasoning lease is released before tool dispatch to prevent nested lock deadlock."""
        tool_executed = False

        async def fake_tool_requiring_vlm() -> str:
            nonlocal tool_executed
            # Tool needs VLM lease
            async with resource_manager.acquire(ModelRole.VLM, required_vram_mb=3500):
                tool_executed = True
                return "vision_result"

        # Simulate Agent loop: lease for thinking, release, then call tool
        async with resource_manager.acquire(ModelRole.REASONING, required_vram_mb=3500):
            pass

        # Lease released; now call tool
        result = await fake_tool_requiring_vlm()
        assert tool_executed is True
        assert result == "vision_result"
        assert resource_manager.allocated_vram_mb == 0

    @pytest.mark.anyio
    async def test_nested_agent_tool_vision_does_not_deadlock(
        self, resource_manager: ResourceManager
    ) -> None:
        """Sequential Agent -> Tool -> Vision execution under resource constraints does not deadlock."""
        log: list[str] = []

        async def run_pipeline() -> None:
            # Step 1: LLM Reasoning
            async with resource_manager.acquire(ModelRole.REASONING, required_vram_mb=3000):
                log.append("reasoning_done")

            # Step 2: Tool execution
            log.append("tool_start")
            # Step 3: Vision analysis inside tool
            async with resource_manager.acquire(ModelRole.VLM, required_vram_mb=3000):
                log.append("vision_done")

            # Step 4: Final LLM synthesis
            async with resource_manager.acquire(ModelRole.REASONING, required_vram_mb=3000):
                log.append("synthesis_done")

        # Run with a strict timeout to catch any deadlock
        await asyncio.wait_for(run_pipeline(), timeout=2.0)

        assert log == ["reasoning_done", "tool_start", "vision_done", "synthesis_done"]
        assert resource_manager.allocated_vram_mb == 0

    @pytest.mark.anyio
    async def test_cancellation_of_one_waiter_does_not_block_others(
        self, resource_manager: ResourceManager
    ) -> None:
        """Cancellation of one waiter does not prevent unrelated waiters from progressing."""
        holder_started = asyncio.Event()
        holder_release = asyncio.Event()
        completed: list[int] = []

        async def holder() -> None:
            async with resource_manager.acquire(ModelRole.REASONING, required_vram_mb=3000):
                holder_started.set()
                await holder_release.wait()

        async def waiter(idx: int) -> None:
            await holder_started.wait()
            async with resource_manager.acquire(ModelRole.REASONING, required_vram_mb=3000):
                completed.append(idx)

        t_holder = asyncio.create_task(holder())
        await holder_started.wait()

        t_w1 = asyncio.create_task(waiter(1))
        t_w2 = asyncio.create_task(waiter(2))  # Will be cancelled
        t_w3 = asyncio.create_task(waiter(3))

        await asyncio.sleep(0.01)

        # Cancel waiter 2
        t_w2.cancel()

        holder_release.set()

        try:
            await asyncio.gather(t_holder, t_w1, t_w3)
        finally:
            for t in [t_holder, t_w1, t_w2, t_w3]:
                if not t.done():
                    t.cancel()

        assert 1 in completed
        assert 3 in completed
        assert 2 not in completed
        assert resource_manager.allocated_vram_mb == 0


# ============================================================================
# Section E: Repeated Concurrency Cycles (100+ Cycles)
# ============================================================================


class TestRepeatedConcurrencyCycles:
    """Run at least 100 deterministic contention cycles with strict invariant assertions."""

    @pytest.mark.anyio
    async def test_100_deterministic_contention_cycles_with_invariants(
        self, resource_manager: ResourceManager
    ) -> None:
        """Execute 100 adversarial contention cycles and verify invariants after every cycle."""
        roles = [ModelRole.REASONING, ModelRole.VLM, ModelRole.GENERAL, ModelRole.ROUTER]
        priorities = [
            TaskPriority.VOICE_INTERACTIVE,
            TaskPriority.USER_INTERACTIVE,
            TaskPriority.ROUTING,
            TaskPriority.BACKGROUND,
        ]

        total_cycles = 100

        for cycle in range(total_cycles):
            # In each cycle, spawn 4 competing tasks with varied roles and priorities
            tasks = []
            cycle_completed = []

            async def cycle_worker(
                task_id: int,
                r: ModelRole,
                p: TaskPriority,
                should_cancel: bool,
                completed: list[int],
            ) -> None:
                try:
                    async with resource_manager.acquire(
                        r, required_vram_mb=1500, required_ram_mb=500, priority=p
                    ):
                        if should_cancel:
                            raise asyncio.CancelledError()
                        await asyncio.sleep(0.001)
                        completed.append(task_id)
                except (asyncio.CancelledError, InsufficientResourceError, ResourceLockError):
                    pass

            for i in range(4):
                role = roles[(cycle + i) % len(roles)]
                priority = priorities[(cycle + i) % len(priorities)]
                should_cancel = (cycle + i) % 7 == 0  # Deterministic sporadic cancellation
                tasks.append(
                    asyncio.create_task(
                        cycle_worker(i, role, priority, should_cancel, cycle_completed)
                    )
                )

            try:
                await asyncio.gather(*tasks)
            finally:
                for t in tasks:
                    if not t.done():
                        t.cancel()

            # Assert strict invariants after EVERY cycle
            assert resource_manager.allocated_vram_mb == 0, (
                f"Cycle {cycle}: VRAM leaked! allocated={resource_manager.allocated_vram_mb}"
            )
            assert resource_manager.allocated_ram_mb == 0, (
                f"Cycle {cycle}: RAM leaked! allocated={resource_manager.allocated_ram_mb}"
            )
            assert resource_manager.active_leases_count == 0, (
                f"Cycle {cycle}: Active leases leaked! count={resource_manager.active_leases_count}"
            )
            assert not resource_manager.is_heavy_inference_active, (
                f"Cycle {cycle}: Heavy inference lock remains held!"
            )
            assert not resource_manager._heavy_lock_reserved, (
                f"Cycle {cycle}: Heavy lock reserved flag remained True!"
            )
            assert resource_manager._current_heavy_vram_mb == 0, (
                f"Cycle {cycle}: Heavy VRAM tracking not reset!"
            )
            assert await resource_manager._priority_queue.get_waiters_count() == 0, (
                f"Cycle {cycle}: Waiters abandoned in queue!"
            )
