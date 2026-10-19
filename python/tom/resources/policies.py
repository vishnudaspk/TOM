"""Resource Policies: Priority Arbitration, Starvation Aging, and Queueing.

Defines:
- TaskPriority: Priority levels from interactive to background.
- ResourceWaiter: Bookkeeping for pending resource acquisition requests.
- PriorityResourceQueue: Priority queue with deterministic starvation aging and FIFO
  ordering for equal priority.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum

from tom.resources.types import ModelRole


class TaskPriority(IntEnum):
    """Task priorities for resource arbitration (lower numeric value = higher priority)."""

    VOICE_INTERACTIVE = 0  # Ultra-low latency voice requests
    USER_INTERACTIVE = 1  # Interactive user chat and direct commands
    ROUTING = 2  # Intent routing / classification
    REASONING = 3  # Deep reasoning steps
    VISION = 4  # VLM analysis
    BACKGROUND = 5  # Proactive tasks, indexing, cleanup


@dataclass
class ResourceWaiter:
    """Represents a queued resource acquisition request waiting for capacity."""

    waiter_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    priority: TaskPriority = TaskPriority.USER_INTERACTIVE
    created_at: float = field(default_factory=time.time)
    sequence: int = 0
    role: ModelRole = ModelRole.GENERAL
    vram_mb: int = 0
    ram_mb: int = 0
    ready_event: asyncio.Event = field(default_factory=asyncio.Event)
    granted: bool = False
    cancelled: bool = False

    def get_effective_priority(self, now: float, aging_threshold_seconds: float = 15.0) -> int:
        """Calculate starvation-aged priority.

        For each aging_threshold_seconds spent waiting, priority improves by 1,
        bounded at 0 (VOICE_INTERACTIVE).
        """
        if aging_threshold_seconds <= 0:
            return int(self.priority)
        waited = max(0.0, now - self.created_at)
        boost = int(waited // aging_threshold_seconds)
        return max(0, int(self.priority) - boost)


class PriorityResourceQueue:
    """Thread-safe priority queue for resource acquisition requests.

    Supports:
    - Higher priority outranking lower priority
    - Strict FIFO sequence ordering for equal effective priorities
    - Deterministic, bounded starvation aging
    - Safe waiter cancellation without queue corruption
    """

    def __init__(
        self,
        aging_threshold_seconds: float = 15.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.aging_threshold_seconds = aging_threshold_seconds
        self._clock = clock or time.time
        self._waiters: dict[str, ResourceWaiter] = {}
        self._next_sequence: int = 0
        self._lock = asyncio.Lock()

    async def enqueue(
        self,
        role: ModelRole,
        vram_mb: int = 0,
        ram_mb: int = 0,
        priority: TaskPriority = TaskPriority.USER_INTERACTIVE,
    ) -> ResourceWaiter:
        """Enqueue a new resource waiter."""
        async with self._lock:
            waiter = ResourceWaiter(
                priority=priority,
                created_at=self._clock(),
                sequence=self._next_sequence,
                role=role,
                vram_mb=vram_mb,
                ram_mb=ram_mb,
            )
            self._next_sequence += 1
            self._waiters[waiter.waiter_id] = waiter
            return waiter

    async def remove(self, waiter_id: str) -> bool:
        """Remove a waiter by ID (e.g. upon cancellation or timeout)."""
        async with self._lock:
            waiter = self._waiters.pop(waiter_id, None)
            if waiter is not None:
                waiter.cancelled = True
                return True
            return False

    async def peek_next(self, now: float | None = None) -> ResourceWaiter | None:
        """Peek at the highest-priority waiter without removing it."""
        async with self._lock:
            if not self._waiters:
                return None
            current_time = now if now is not None else self._clock()
            sorted_waiters = sorted(
                self._waiters.values(),
                key=lambda w: (
                    w.get_effective_priority(current_time, self.aging_threshold_seconds),
                    w.sequence,
                ),
            )
            return sorted_waiters[0]

    async def pop_admissible(
        self,
        can_admit_fn: Callable[[ResourceWaiter], bool],
        now: float | None = None,
    ) -> ResourceWaiter | None:
        """Find the highest-priority waiter that satisfies can_admit_fn, pop and return it.

        Guarantees:
        - Only one waiter receives the capacity opportunity.
        - Preserves priority and FIFO sequence ordering.
        """
        async with self._lock:
            if not self._waiters:
                return None
            current_time = now if now is not None else self._clock()
            sorted_waiters = sorted(
                self._waiters.values(),
                key=lambda w: (
                    w.get_effective_priority(current_time, self.aging_threshold_seconds),
                    w.sequence,
                ),
            )
            for waiter in sorted_waiters:
                if not waiter.cancelled and can_admit_fn(waiter):
                    del self._waiters[waiter.waiter_id]
                    waiter.granted = True
                    return waiter
            return None

    async def get_waiters_count(self) -> int:
        """Return the number of active waiters in the queue."""
        async with self._lock:
            return len(self._waiters)

    async def list_waiters(self) -> list[ResourceWaiter]:
        """Return a snapshot list of current waiters."""
        async with self._lock:
            return list(self._waiters.values())
