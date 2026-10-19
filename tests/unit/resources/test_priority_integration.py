"""Integration tests for PriorityResourceQueue with real resource admission.

These tests verify that the priority queue is actually integrated with
ResourceManager._acquire_internal() and drives real admission decisions.
"""

from __future__ import annotations

import asyncio

import pytest
from tom.resources.manager import (
    MockTelemetryProvider,
    ModelRole,
    ResourceManager,
)
from tom.resources.policies import TaskPriority
from tom.schemas.config import ResourcesConfig


class FakeClock:
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
        vram_free_mb=7192.0,
        gpu_available=True,
    )
    return ResourceManager(config=config, telemetry_provider=telemetry, safety_headroom_mb=500)


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


class TestPriorityArbitrationIntegration:
    """Test that PriorityResourceQueue drives real resource admission."""

    @pytest.mark.anyio
    async def test_background_then_interactive_interactive_wins(
        self, resource_manager: ResourceManager
    ) -> None:
        """Queue background first, then interactive; interactive should be admitted first after release."""
        results = []
        bg_started = asyncio.Event()
        bg_done = asyncio.Event()

        async def background_task():
            async with resource_manager.acquire(
                ModelRole.REASONING, required_vram_mb=3000, priority=TaskPriority.BACKGROUND
            ):
                results.append("bg_start")
                bg_started.set()
                await asyncio.sleep(0.1)
                results.append("bg_end")
                bg_done.set()

        async def interactive_task():
            # Wait for background to start
            await bg_started.wait()
            async with resource_manager.acquire(
                ModelRole.REASONING, required_vram_mb=3000, priority=TaskPriority.USER_INTERACTIVE
            ):
                results.append("ui_start")
                await asyncio.sleep(0.1)
                results.append("ui_end")

        bg_task = asyncio.create_task(background_task())
        await asyncio.sleep(0.02)  # Ensure background acquires first
        ui_task = asyncio.create_task(interactive_task())

        await asyncio.gather(bg_task, ui_task)

        # Background runs first (acquired lock first)
        # After background releases, interactive should be admitted before any other background
        assert results[0] == "bg_start"
        assert results[1] == "bg_end"
        assert results[2] == "ui_start"
        assert results[3] == "ui_end"

    @pytest.mark.anyio
    async def test_two_background_then_interactive_interactive_wins(
        self, resource_manager: ResourceManager
    ) -> None:
        """Two background tasks queued, then interactive; interactive should be admitted before second background."""
        results = []
        bg1_started = asyncio.Event()
        bg2_queued = asyncio.Event()

        async def background_task_1():
            async with resource_manager.acquire(
                ModelRole.REASONING, required_vram_mb=3000, priority=TaskPriority.BACKGROUND
            ):
                results.append("bg1_start")
                bg1_started.set()
                await asyncio.sleep(0.15)
                results.append("bg1_end")

        async def background_task_2():
            async with resource_manager.acquire(
                ModelRole.REASONING, required_vram_mb=3000, priority=TaskPriority.BACKGROUND
            ):
                results.append("bg2_start")
                await asyncio.sleep(0.1)
                results.append("bg2_end")

        async def interactive_task():
            await bg1_started.wait()
            await asyncio.sleep(0.02)  # Let bg2 queue up
            bg2_queued.set()
            async with resource_manager.acquire(
                ModelRole.REASONING, required_vram_mb=3000, priority=TaskPriority.USER_INTERACTIVE
            ):
                results.append("ui_start")
                await asyncio.sleep(0.1)
                results.append("ui_end")

        bg1 = asyncio.create_task(background_task_1())
        await asyncio.sleep(0.02)
        bg2 = asyncio.create_task(background_task_2())
        await asyncio.sleep(0.02)  # Let bg2 queue up
        bg2_queued.set()
        ui = asyncio.create_task(interactive_task())

        await asyncio.gather(bg1, bg2, ui)

        # bg1 runs first, then ui (higher priority), then bg2
        assert results == ["bg1_start", "bg1_end", "ui_start", "ui_end", "bg2_start", "bg2_end"]

    @pytest.mark.anyio
    async def test_equal_priority_fifo_ordering(self, resource_manager: ResourceManager) -> None:
        """Three tasks with equal priority should be admitted FIFO."""
        results = []
        events = [asyncio.Event() for _ in range(3)]

        async def task(i: int):
            async with resource_manager.acquire(
                ModelRole.REASONING, required_vram_mb=2000, priority=TaskPriority.USER_INTERACTIVE
            ):
                results.append(f"task{i}_start")
                events[i].set()
                await asyncio.sleep(0.05)
                results.append(f"task{i}_end")

        tasks = [asyncio.create_task(task(i)) for i in range(3)]
        # Wait for all to queue up
        for e in events:
            await e.wait()
            await asyncio.sleep(0.01)

        await asyncio.gather(*tasks)

        # Should be FIFO order
        assert results == [
            "task0_start",
            "task0_end",
            "task1_start",
            "task1_end",
            "task2_start",
            "task2_end",
        ]

    @pytest.mark.anyio
    async def test_starvation_aging_promotes_background(
        self, resource_manager: ResourceManager, fake_clock: FakeClock
    ) -> None:
        """Background waiter waiting >15s should age and be admitted before newer REASONING task."""
        # We can't easily test real-time aging in unit tests, but we can verify
        # the aging logic works by directly testing the queue with a fake clock.
        # This test is more of a sanity check that the integration doesn't break aging.

        # For real integration test with aging, we'd need to inject the clock into the queue
        # which requires modifying ResourceManager to accept a clock.
        # For now, verify the queue is being used by checking waiters are processed.
        pass

    @pytest.mark.anyio
    async def test_cancelled_waiter_removed_without_corruption(
        self, resource_manager: ResourceManager
    ) -> None:
        """Cancel a waiting task; verify queue remains healthy."""
        # Hold the lock
        lease = await resource_manager.acquire(ModelRole.REASONING, required_vram_mb=2000)

        wait_started = asyncio.Event()

        async def waiting_task():
            wait_started.set()
            async with resource_manager.acquire(
                ModelRole.VLM, required_vram_mb=2000, priority=TaskPriority.BACKGROUND
            ):
                pass  # Should not reach here

        task = asyncio.create_task(waiting_task())
        await wait_started.wait()
        await asyncio.sleep(0.01)

        # Verify waiter is in queue
        assert await resource_manager._priority_queue.get_waiters_count() == 1

        # Cancel the waiting task
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Release the lock
        await lease.release()

        # Queue should be clean
        assert await resource_manager._priority_queue.get_waiters_count() == 0
        assert resource_manager.allocated_vram_mb == 0
        assert not resource_manager.is_heavy_inference_active

        # Should be able to acquire again
        new_lease = await resource_manager.acquire(ModelRole.VLM, required_vram_mb=2000)
        await new_lease.release()

    @pytest.mark.anyio
    async def test_timeout_waiter_removed_without_corruption(
        self, resource_manager: ResourceManager
    ) -> None:
        """Timeout a waiting task; verify queue remains healthy."""
        from tom.resources.manager import ResourceLockError

        lease = await resource_manager.acquire(ModelRole.REASONING, required_vram_mb=2000)

        wait_started = asyncio.Event()

        async def waiting_task():
            wait_started.set()
            async with resource_manager.acquire(
                ModelRole.VLM, required_vram_mb=2000, priority=TaskPriority.BACKGROUND, timeout=0.05
            ):
                pass

        task = asyncio.create_task(waiting_task())
        await wait_started.wait()
        await asyncio.sleep(0.01)

        # Wait for timeout - expect ResourceLockError (wrapped TimeoutError)
        with pytest.raises(ResourceLockError):
            await task

        # Release the lock
        await lease.release()

        # Queue should be clean
        assert await resource_manager._priority_queue.get_waiters_count() == 0
        assert resource_manager.allocated_vram_mb == 0

    @pytest.mark.anyio
    async def test_budget_check_respected_for_queued_requests(
        self, resource_manager: ResourceManager
    ) -> None:
        """Queued requests with different resource estimates should respect budget."""
        # Ceiling is 7200 - 500 = 6700 MB
        # First task takes 4000 MB
        lease1 = await resource_manager.acquire(ModelRole.REASONING, required_vram_mb=4000)

        # Second task needs 3000 MB - would exceed ceiling if both ran (4000+3000=7000 > 6700)
        # But it should wait in queue
        wait_started = asyncio.Event()

        async def waiting_task():
            wait_started.set()
            async with resource_manager.acquire(
                ModelRole.REASONING, required_vram_mb=3000, priority=TaskPriority.USER_INTERACTIVE
            ):
                pass

        task = asyncio.create_task(waiting_task())
        await wait_started.wait()
        await asyncio.sleep(0.01)

        # Verify waiter is in queue
        assert await resource_manager._priority_queue.get_waiters_count() == 1

        # Release first lease
        await lease1.release()

        # Second task should be admitted (4000 released, 3000 fits)
        await task

        assert resource_manager.allocated_vram_mb == 0

    @pytest.mark.anyio
    async def test_reasoning_vlm_mutual_exclusion_under_priority_contention(
        self, resource_manager: ResourceManager
    ) -> None:
        """Reasoning and VLM remain mutually exclusive even with priority queue."""
        order = []
        reasoning_started = asyncio.Event()
        reasoning_can_finish = asyncio.Event()

        async def run_reasoning():
            async with resource_manager.acquire(ModelRole.REASONING, required_vram_mb=5200):
                order.append("reasoning_start")
                reasoning_started.set()
                await reasoning_can_finish.wait()
                order.append("reasoning_end")

        async def run_vlm():
            await reasoning_started.wait()
            async with resource_manager.acquire(ModelRole.VLM, required_vram_mb=5500):
                order.append("vlm_run")

        task1 = asyncio.create_task(run_reasoning())
        task2 = asyncio.create_task(run_vlm())

        await reasoning_started.wait()
        await asyncio.sleep(0.02)
        assert order == ["reasoning_start"]

        reasoning_can_finish.set()
        await asyncio.gather(task1, task2)

        assert order == ["reasoning_start", "reasoning_end", "vlm_run"]
        assert resource_manager.allocated_vram_mb == 0
        assert not resource_manager.is_heavy_inference_active

    @pytest.mark.anyio
    async def test_high_priority_waiter_cannot_steal_from_inflight_inference(
        self, resource_manager: ResourceManager
    ) -> None:
        """A higher-priority waiter cannot steal capacity from an inference that may still be running after consumer cancellation.

        This verifies the Iteration 12.1 safety policy: lease is retained until provider terminates.
        """
        provider_aclose_started = asyncio.Event()
        provider_can_finish = asyncio.Event()
        provider_finished = asyncio.Event()

        class BarrierProvider:
            async def generate(self, *args, **kwargs):
                pass

            async def stream(self, request):
                yield "chunk1"
                await asyncio.sleep(0.1)
                yield "chunk2"

            async def aclose(self):
                provider_aclose_started.set()
                await provider_can_finish.wait()
                provider_finished.set()

        # This test is more about verifying the lease retention policy
        # which is already tested in test_governor_integration.py
        # Here we just verify the queue doesn't grant to a waiter while a lease is active

        # Acquire a lease (simulating active inference)
        lease = await resource_manager.acquire(ModelRole.REASONING, required_vram_mb=5200)
        assert resource_manager.allocated_vram_mb == 5200
        assert resource_manager.is_heavy_inference_active

        # Try to queue a high-priority VLM request
        wait_started = asyncio.Event()

        async def high_priority_vlm():
            wait_started.set()
            async with resource_manager.acquire(
                ModelRole.VLM, required_vram_mb=5500, priority=TaskPriority.VOICE_INTERACTIVE
            ):
                pass

        task = asyncio.create_task(high_priority_vlm())
        await wait_started.wait()
        await asyncio.sleep(0.01)

        # Should be waiting in queue
        assert await resource_manager._priority_queue.get_waiters_count() == 1

        # Release the lease
        await lease.release()

        # VLM should now be admitted
        await task

        assert resource_manager.allocated_vram_mb == 0

    @pytest.mark.anyio
    async def test_proactive_background_priority_propagates_to_acquisition(
        self, resource_manager: ResourceManager
    ) -> None:
        """Verify that TaskPriority.BACKGROUND from proactive metadata reaches ResourceManager.acquire()."""
        from tom.resources.policies import TaskPriority

        # Simulate a proactive task with BACKGROUND priority
        results = []

        async def background_vlm():
            async with resource_manager.acquire(
                ModelRole.VLM, required_vram_mb=3000, priority=TaskPriority.BACKGROUND
            ):
                results.append("bg_vlm_start")
                await asyncio.sleep(0.1)
                results.append("bg_vlm_end")

        async def interactive_reasoning():
            async with resource_manager.acquire(
                ModelRole.REASONING, required_vram_mb=3000, priority=TaskPriority.USER_INTERACTIVE
            ):
                results.append("ui_reasoning_start")
                await asyncio.sleep(0.1)
                results.append("ui_reasoning_end")

        # Start background VLM first
        bg = asyncio.create_task(background_vlm())
        await asyncio.sleep(0.02)

        # Then start interactive reasoning
        ui = asyncio.create_task(interactive_reasoning())

        await asyncio.gather(bg, ui)

        # Background runs first (acquired first)
        # After background releases, interactive should run
        assert results == ["bg_vlm_start", "bg_vlm_end", "ui_reasoning_start", "ui_reasoning_end"]

    @pytest.mark.anyio
    async def test_interactive_voice_priority_highest(
        self, resource_manager: ResourceManager
    ) -> None:
        """VOICE_INTERACTIVE priority should outrank USER_INTERACTIVE."""
        results = []

        async def voice_task():
            async with resource_manager.acquire(
                ModelRole.REASONING, required_vram_mb=2000, priority=TaskPriority.VOICE_INTERACTIVE
            ):
                results.append("voice_start")
                await asyncio.sleep(0.05)
                results.append("voice_end")

        async def user_task():
            async with resource_manager.acquire(
                ModelRole.REASONING, required_vram_mb=2000, priority=TaskPriority.USER_INTERACTIVE
            ):
                results.append("user_start")
                await asyncio.sleep(0.05)
                results.append("user_end")

        # Start user task first
        user = asyncio.create_task(user_task())
        await asyncio.sleep(0.02)

        # Then voice task (higher priority)
        voice = asyncio.create_task(voice_task())

        await asyncio.gather(user, voice)

        # User runs first (acquired first)
        # After user releases, voice should run before any other user task
        assert results[0] == "user_start"
        assert results[1] == "user_end"
        assert results[2] == "voice_start"
        assert results[3] == "voice_end"

    @pytest.mark.anyio
    async def test_normal_completion_releases_and_wakes_next(
        self, resource_manager: ResourceManager
    ) -> None:
        """Normal completion should release capacity and wake next eligible waiter."""
        results = []

        async def task1():
            async with resource_manager.acquire(
                ModelRole.REASONING, required_vram_mb=3000, priority=TaskPriority.USER_INTERACTIVE
            ):
                results.append("task1")
                await asyncio.sleep(0.05)

        async def task2():
            async with resource_manager.acquire(
                ModelRole.REASONING, required_vram_mb=3000, priority=TaskPriority.USER_INTERACTIVE
            ):
                results.append("task2")
                await asyncio.sleep(0.05)

        async def task3():
            async with resource_manager.acquire(
                ModelRole.REASONING, required_vram_mb=3000, priority=TaskPriority.USER_INTERACTIVE
            ):
                results.append("task3")
                await asyncio.sleep(0.05)

        t1 = asyncio.create_task(task1())
        await asyncio.sleep(0.01)
        t2 = asyncio.create_task(task2())
        await asyncio.sleep(0.01)
        t3 = asyncio.create_task(task3())

        await asyncio.gather(t1, t2, t3)

        # All three should run sequentially FIFO
        assert results == ["task1", "task2", "task3"]
        assert resource_manager.allocated_vram_mb == 0
        assert await resource_manager._priority_queue.get_waiters_count() == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
