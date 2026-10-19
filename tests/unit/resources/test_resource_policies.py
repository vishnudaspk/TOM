"""Unit tests for Priority Arbitration & PriorityResourceQueue (Iteration 12.3).

Verifies:
- TaskPriority ordering (lower value = higher priority)
- FIFO ordering for equal-priority waiters
- Deterministic starvation aging with injected clock
- Safe waiter cancellation without queue corruption
- pop_admissible selects highest-priority admissible waiter
- Only one waiter receives each capacity opportunity
- peek_next reflects priority ordering
- Queue count and listing
- Cooperative preemption semantics documented accurately (no forced abort)
"""

from __future__ import annotations

import asyncio

import pytest
from tom.resources.manager import ModelRole
from tom.resources.policies import (
    PriorityResourceQueue,
    ResourceWaiter,
    TaskPriority,
)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def time(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def queue(clock: FakeClock) -> PriorityResourceQueue:
    return PriorityResourceQueue(aging_threshold_seconds=15.0, clock=clock.time)


class TestTaskPriorityOrdering:
    def test_lower_value_means_higher_priority(self) -> None:
        assert TaskPriority.VOICE_INTERACTIVE < TaskPriority.USER_INTERACTIVE
        assert TaskPriority.USER_INTERACTIVE < TaskPriority.ROUTING
        assert TaskPriority.ROUTING < TaskPriority.REASONING
        assert TaskPriority.REASONING < TaskPriority.VISION
        assert TaskPriority.VISION < TaskPriority.BACKGROUND

    def test_voice_interactive_is_highest_priority(self) -> None:
        for p in list(TaskPriority):
            assert TaskPriority.VOICE_INTERACTIVE <= p

    def test_background_is_lowest_priority(self) -> None:
        for p in list(TaskPriority):
            assert TaskPriority.BACKGROUND >= p


class TestResourceWaiterEffectivePriority:
    def test_effective_priority_without_aging(self) -> None:
        w = ResourceWaiter(
            priority=TaskPriority.BACKGROUND,
            created_at=1000.0,
        )
        # At t=1000 no time has elapsed
        assert w.get_effective_priority(1000.0, aging_threshold_seconds=15.0) == int(
            TaskPriority.BACKGROUND
        )

    def test_aging_boosts_priority_after_threshold(self) -> None:
        w = ResourceWaiter(
            priority=TaskPriority.BACKGROUND,  # 5
            created_at=1000.0,
        )
        # After exactly 15s: 1 boost -> priority 4 (VISION)
        assert w.get_effective_priority(1015.0, aging_threshold_seconds=15.0) == 4

    def test_aging_boosts_multiple_steps(self) -> None:
        w = ResourceWaiter(
            priority=TaskPriority.BACKGROUND,  # 5
            created_at=1000.0,
        )
        # After 45s: 3 boosts -> priority 2 (ROUTING)
        assert w.get_effective_priority(1045.0, aging_threshold_seconds=15.0) == 2

    def test_aging_bounded_at_zero(self) -> None:
        w = ResourceWaiter(
            priority=TaskPriority.BACKGROUND,  # 5
            created_at=1000.0,
        )
        # After 1000s (many thresholds): bounded at 0
        assert w.get_effective_priority(2000.0, aging_threshold_seconds=15.0) == 0

    def test_high_priority_not_negatively_affected(self) -> None:
        w = ResourceWaiter(
            priority=TaskPriority.VOICE_INTERACTIVE,  # 0
            created_at=1000.0,
        )
        assert w.get_effective_priority(1000.0, aging_threshold_seconds=15.0) == 0
        assert w.get_effective_priority(2000.0, aging_threshold_seconds=15.0) == 0


class TestPriorityResourceQueueOrdering:
    @pytest.mark.anyio
    async def test_higher_priority_dequeued_first(
        self, queue: PriorityResourceQueue, clock: FakeClock
    ) -> None:
        """Background waiter enqueued first, interactive waiter second;
        interactive must be selected first."""
        bg = await queue.enqueue(ModelRole.GENERAL, vram_mb=0, priority=TaskPriority.BACKGROUND)
        ui = await queue.enqueue(
            ModelRole.GENERAL, vram_mb=0, priority=TaskPriority.USER_INTERACTIVE
        )

        def always_admit(w: ResourceWaiter) -> bool:
            return True

        first = await queue.pop_admissible(always_admit)
        assert first is not None
        assert first.waiter_id == ui.waiter_id
        assert first.priority == TaskPriority.USER_INTERACTIVE

        second = await queue.pop_admissible(always_admit)
        assert second is not None
        assert second.waiter_id == bg.waiter_id

    @pytest.mark.anyio
    async def test_fifo_for_equal_priority(self, queue: PriorityResourceQueue) -> None:
        """Three waiters with equal priority; FIFO order must be preserved."""
        w1 = await queue.enqueue(ModelRole.GENERAL, priority=TaskPriority.REASONING)
        w2 = await queue.enqueue(ModelRole.GENERAL, priority=TaskPriority.REASONING)
        w3 = await queue.enqueue(ModelRole.GENERAL, priority=TaskPriority.REASONING)

        def always_admit(w: ResourceWaiter) -> bool:
            return True

        order = []
        for _ in range(3):
            w = await queue.pop_admissible(always_admit)
            assert w is not None
            order.append(w.waiter_id)

        assert order == [w1.waiter_id, w2.waiter_id, w3.waiter_id]

    @pytest.mark.anyio
    async def test_peek_next_does_not_consume(self, queue: PriorityResourceQueue) -> None:
        _bg = await queue.enqueue(ModelRole.GENERAL, priority=TaskPriority.BACKGROUND)
        vi = await queue.enqueue(ModelRole.GENERAL, priority=TaskPriority.VOICE_INTERACTIVE)

        peeked = await queue.peek_next()
        assert peeked is not None
        assert peeked.waiter_id == vi.waiter_id

        # Queue unchanged
        count = await queue.get_waiters_count()
        assert count == 2

    @pytest.mark.anyio
    async def test_starvation_aging_promotes_background(
        self, queue: PriorityResourceQueue, clock: FakeClock
    ) -> None:
        """Background waiter waiting > aging_threshold eventually outranks a
        newly enqueued REASONING waiter."""
        # Enqueue background first (t=1000)
        bg = await queue.enqueue(ModelRole.GENERAL, priority=TaskPriority.BACKGROUND)

        # Advance 45 seconds: 3 boosts -> effective priority 2 (ROUTING)
        clock.advance(45.0)

        # Enqueue REASONING (effective priority 3) after aging
        _reasoning = await queue.enqueue(ModelRole.GENERAL, priority=TaskPriority.REASONING)

        def always_admit(w: ResourceWaiter) -> bool:
            return True

        # Background has effective priority 2 < REASONING 3, so background is first
        first = await queue.pop_admissible(always_admit)
        assert first is not None
        assert first.waiter_id == bg.waiter_id

    @pytest.mark.anyio
    async def test_pop_admissible_skips_non_admissible_waiters(
        self, queue: PriorityResourceQueue
    ) -> None:
        """If the highest-priority waiter cannot be admitted, the next eligible
        one is selected instead."""
        _high = await queue.enqueue(
            ModelRole.VLM, vram_mb=5500, priority=TaskPriority.VOICE_INTERACTIVE
        )
        low = await queue.enqueue(ModelRole.ROUTER, vram_mb=500, priority=TaskPriority.BACKGROUND)

        # Admit only if vram_mb <= 1000 (VLM blocked, ROUTER admitted)
        def small_only(w: ResourceWaiter) -> bool:
            return w.vram_mb <= 1000

        admitted = await queue.pop_admissible(small_only)
        assert admitted is not None
        assert admitted.waiter_id == low.waiter_id

        # High-priority waiter still in queue
        count = await queue.get_waiters_count()
        assert count == 1

    @pytest.mark.anyio
    async def test_empty_queue_returns_none(self, queue: PriorityResourceQueue) -> None:
        result = await queue.pop_admissible(lambda _: True)
        assert result is None

        peeked = await queue.peek_next()
        assert peeked is None


class TestWaiterCancellation:
    @pytest.mark.anyio
    async def test_remove_waiter_marks_cancelled(self, queue: PriorityResourceQueue) -> None:
        w = await queue.enqueue(ModelRole.GENERAL, priority=TaskPriority.BACKGROUND)

        removed = await queue.remove(w.waiter_id)
        assert removed is True
        assert w.cancelled is True

        count = await queue.get_waiters_count()
        assert count == 0

    @pytest.mark.anyio
    async def test_remove_nonexistent_returns_false(self, queue: PriorityResourceQueue) -> None:
        result = await queue.remove("does-not-exist")
        assert result is False

    @pytest.mark.anyio
    async def test_cancelled_waiter_not_admitted(self, queue: PriorityResourceQueue) -> None:
        """Once a waiter is cancelled, pop_admissible must not select it."""
        w = await queue.enqueue(ModelRole.GENERAL, priority=TaskPriority.BACKGROUND)
        # Manually mark as cancelled (simulating race condition)
        w.cancelled = True

        result = await queue.pop_admissible(lambda _: True)
        assert result is None

    @pytest.mark.anyio
    async def test_cancel_one_of_several_leaves_rest_intact(
        self, queue: PriorityResourceQueue
    ) -> None:
        w1 = await queue.enqueue(ModelRole.GENERAL, priority=TaskPriority.REASONING)
        w2 = await queue.enqueue(ModelRole.GENERAL, priority=TaskPriority.REASONING)
        w3 = await queue.enqueue(ModelRole.GENERAL, priority=TaskPriority.REASONING)

        await queue.remove(w2.waiter_id)

        order = []
        while True:
            w = await queue.pop_admissible(lambda _: True)
            if w is None:
                break
            order.append(w.waiter_id)

        assert order == [w1.waiter_id, w3.waiter_id]
        assert w2.waiter_id not in order


class TestExactlyOnceGrant:
    @pytest.mark.anyio
    async def test_only_one_waiter_admitted_per_pop(self, queue: PriorityResourceQueue) -> None:
        """Concurrent pop_admissible calls must each admit at most one waiter."""
        for _ in range(5):
            await queue.enqueue(ModelRole.GENERAL, priority=TaskPriority.USER_INTERACTIVE)

        results = await asyncio.gather(
            queue.pop_admissible(lambda _: True),
            queue.pop_admissible(lambda _: True),
            queue.pop_admissible(lambda _: True),
        )

        granted = [r for r in results if r is not None]
        ids = [w.waiter_id for w in granted]
        # All granted IDs must be distinct
        assert len(ids) == len(set(ids))
        # Remaining queue has 2 left
        count = await queue.get_waiters_count()
        assert count == 2

    @pytest.mark.anyio
    async def test_granted_flag_set_on_admitted_waiter(self, queue: PriorityResourceQueue) -> None:
        w = await queue.enqueue(ModelRole.GENERAL, priority=TaskPriority.USER_INTERACTIVE)

        admitted = await queue.pop_admissible(lambda _: True)
        assert admitted is not None
        assert admitted.granted is True
        assert admitted.waiter_id == w.waiter_id


class TestCooperativePreemptionSemantics:
    """Verify the documented cooperative preemption contract.

    Cooperative preemption is a *notification*, not a forced abort.
    The preemption mechanism signals eligible background waiters to yield
    at safe points. It does NOT guarantee immediate yield, does NOT forcibly
    terminate running inference, and does NOT free GPU capacity that may
    still be consumed by an active operation.
    """

    def test_background_priority_value_is_lowest(self) -> None:
        """TaskPriority.BACKGROUND is the lowest priority by design."""
        assert int(TaskPriority.BACKGROUND) == max(int(p) for p in TaskPriority)

    def test_voice_interactive_priority_is_highest(self) -> None:
        assert int(TaskPriority.VOICE_INTERACTIVE) == min(int(p) for p in TaskPriority)

    @pytest.mark.anyio
    async def test_interactive_request_outranks_queued_background(
        self, queue: PriorityResourceQueue
    ) -> None:
        """Interactive request wins over a background request in the queue,
        demonstrating priority-ordered admission without forced preemption."""
        bg1 = await queue.enqueue(ModelRole.GENERAL, priority=TaskPriority.BACKGROUND)
        bg2 = await queue.enqueue(ModelRole.GENERAL, priority=TaskPriority.BACKGROUND)
        ui = await queue.enqueue(ModelRole.GENERAL, priority=TaskPriority.USER_INTERACTIVE)

        def always_admit(w: ResourceWaiter) -> bool:
            return True

        # Interactive must be admitted first even though it was enqueued last
        first = await queue.pop_admissible(always_admit)
        assert first is not None
        assert first.priority == TaskPriority.USER_INTERACTIVE
        assert first.waiter_id == ui.waiter_id

        # Background waiters in FIFO order follow
        second = await queue.pop_admissible(always_admit)
        assert second is not None
        assert second.waiter_id == bg1.waiter_id

        third = await queue.pop_admissible(always_admit)
        assert third is not None
        assert third.waiter_id == bg2.waiter_id

    @pytest.mark.anyio
    async def test_list_waiters_returns_snapshot(self, queue: PriorityResourceQueue) -> None:
        w1 = await queue.enqueue(ModelRole.GENERAL, priority=TaskPriority.BACKGROUND)
        w2 = await queue.enqueue(ModelRole.GENERAL, priority=TaskPriority.USER_INTERACTIVE)

        snapshot = await queue.list_waiters()
        ids = {w.waiter_id for w in snapshot}
        assert {w1.waiter_id, w2.waiter_id} == ids
