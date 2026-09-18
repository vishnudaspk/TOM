"""Cooperative cancellation primitive for async task pipelines.

Adheres to:
- PHASE4_IMPLEMENTATIONPLAN.md §5 (Iteration 5)
- Decision 035: Step-Bounded Execution Loop & Cooperative Task Cancellation
"""

from __future__ import annotations

import asyncio


class CancellationToken:
    """Cooperative cancellation primitive wrapping ``asyncio.Event``.

    A ``CancellationToken`` is shared across all tasks in an agent workflow.
    When ``cancel()`` is called, the event is set and all tasks that check
    ``is_cancelled()`` or await the event will abort cooperatively.

    Usage:
        token = CancellationToken()
        task = asyncio.create_task(agent_orchestrator.run("prompt", token))
        token.cancel()  # triggers cooperative cancellation
    """

    def __init__(self) -> None:
        self._event: asyncio.Event = asyncio.Event()

    @property
    def event(self) -> asyncio.Event:
        """Return the underlying asyncio.Event.

        Callers can await this directly to block until cancellation is requested.
        """
        return self._event

    def cancel(self) -> None:
        """Signal all listening tasks to abort cooperatively."""
        self._event.set()

    def is_cancelled(self) -> bool:
        """Return True if cancellation has been requested."""
        return self._event.is_set()
