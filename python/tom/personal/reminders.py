"""ReminderService — scheduler-safe lifecycle bridge for personal reminders.

Adheres to:
- Phase 10 Architecture (personal.reminder + ProactiveScheduler integration)
- Phase 8 hard safety invariants (quiet hours, rate limiting, user preemption,
  mandatory confirmation on mutations) — enforced exclusively by the existing
  ``ProactiveScheduler`` policy pipeline; this module never reimplements them.
- No arbitrary task execution: reminder occurrences dispatch through
  ``ProactiveScheduler.dispatch_proactive_task`` with ``plan=None``, which only
  creates an auditable proactive task record — no plan steps ever execute.

Duplicate suppression is enforced twice:
1. ``IntervalTrigger.is_due`` (scheduler-side, via ``evaluate_policy``).
2. An optimistic ``fires`` guard inside ``PersonalStore.record_reminder_fire``
   advanced transactionally after each successful dispatch.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from tom.personal.store import PersonalStore
from tom.schemas.personal import ReminderDispatch, ReminderRecord, ReminderStatus
from tom.schemas.proactive import IntervalTrigger
from tom.telemetry.logging import get_logger

if TYPE_CHECKING:
    from tom.agents.proactive import ProactiveScheduler

logger = get_logger(__name__, component="personal.reminders")

# Placeholder interval for never-fired triggers; timing is gated by the
# store's next-fire check, so the interval only matters after the first fire.
_NEVER_FIRED_INTERVAL_SECONDS = 3600.0


def build_reminder_trigger(reminder: ReminderRecord) -> IntervalTrigger:
    """Build the ProactiveScheduler trigger representing one reminder.

    The trigger's ``last_run``/``interval_seconds`` are aligned so that
    ``is_due(now)`` is true exactly when ``now >= reminder.fire_utc``:
    - never fired -> ``last_run=None`` (due immediately; service gates timing)
    - fired -> ``last_run=last_fired_at``, ``interval_seconds`` spans to the
      next scheduled occurrence.
    """
    if reminder.last_fired_at is None:
        interval = _NEVER_FIRED_INTERVAL_SECONDS
        last_run = None
    else:
        interval = (reminder.fire_utc - reminder.last_fired_at).total_seconds()
        if interval <= 0:
            interval = _NEVER_FIRED_INTERVAL_SECONDS
        last_run = reminder.last_fired_at

    return IntervalTrigger(
        trigger_id=f"reminder_{reminder.reminder_id}",
        name=f"reminder:{reminder.reminder_id}",
        interval_seconds=interval,
        goal=f"REMINDER: {reminder.message}",
        plan=None,  # reminders never execute arbitrary plans
        urgent_health=False,  # respect existing quiet-hours policy
        enabled=True,
        last_run=last_run,
    )


class ReminderService:
    """Drives due reminders through the existing ProactiveScheduler policy.

    The service owns its triggers (they are intentionally NOT registered in the
    scheduler's global trigger map, so ``ProactiveScheduler.tick`` cannot
    dispatch them without updating reminder persistence). Policy evaluation,
    quiet hours, user preemption, rate limiting, and dispatch bookkeeping all
    come from the injected ``ProactiveScheduler``.
    """

    def __init__(
        self,
        store: PersonalStore,
        scheduler: ProactiveScheduler | None = None,
    ) -> None:
        if scheduler is None:
            # Imported lazily: tom.agents transitively imports tom.tools, which
            # imports this module — a top-level import would create a cycle.
            from tom.agents.proactive import ProactiveScheduler

            scheduler = ProactiveScheduler()
        self.store = store
        self.scheduler = scheduler
        self._triggers: dict[str, IntervalTrigger] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Trigger lifecycle
    # ------------------------------------------------------------------

    def sync_trigger(self, reminder: ReminderRecord) -> IntervalTrigger | None:
        """Create or refresh the local trigger for a reminder.

        Returns None (and drops any existing trigger) when the reminder is no
        longer active (cancelled or completed).
        """
        if reminder.status != ReminderStatus.ACTIVE:
            self._triggers.pop(reminder.reminder_id, None)
            return None
        trigger = build_reminder_trigger(reminder)
        self._triggers[reminder.reminder_id] = trigger
        return trigger

    def drop_trigger(self, reminder_id: str) -> None:
        """Remove the local trigger for a reminder (cancellation/deletion)."""
        self._triggers.pop(reminder_id, None)

    def get_trigger(self, reminder_id: str) -> IntervalTrigger | None:
        """Return the local trigger bound to a reminder, if any."""
        return self._triggers.get(reminder_id)

    def trigger_ids(self) -> list[str]:
        """Return reminder ids that currently hold a local trigger."""
        return sorted(self._triggers)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def tick(self, now: datetime | None = None) -> list[ReminderDispatch]:
        """Fire every due reminder through the ProactiveScheduler policy.

        For each active, due reminder:
        1. Build/refresh its trigger.
        2. ``scheduler.dispatch_proactive_task`` applies the existing policy
           order (enabled -> user preemption -> quiet hours -> rate limit ->
           trigger due). A blocked dispatch defers the reminder untouched.
        3. On success, consume the occurrence transactionally in SQLite
           (duplicate-suppressed) and resync the trigger, or drop it when the
           reminder completed.
        """
        current_time = now or datetime.now(UTC)
        dispatched: list[ReminderDispatch] = []

        async with self._lock:
            due = await asyncio.to_thread(self.store.due_reminders, current_time)
            for reminder in due:
                trigger = self.sync_trigger(reminder)
                if trigger is None:
                    continue

                task = await self.scheduler.dispatch_proactive_task(trigger, current_time)
                if task is None:
                    # Blocked by existing scheduler policy (quiet hours,
                    # preemption, rate limit, disabled, or not due).
                    logger.info(
                        "reminder_dispatch_deferred",
                        reminder_id=reminder.reminder_id,
                    )
                    continue

                occurrence = reminder.fire_utc
                updated = await asyncio.to_thread(
                    self.store.record_reminder_fire,
                    reminder.reminder_id,
                    reminder.fires,
                    occurrence,
                )
                if updated is None:
                    # Another path already consumed this occurrence.
                    logger.info(
                        "reminder_duplicate_suppressed",
                        reminder_id=reminder.reminder_id,
                    )
                    continue

                self.sync_trigger(updated)
                dispatched.append(
                    ReminderDispatch(
                        reminder_id=reminder.reminder_id,
                        message=reminder.message,
                        occurrence_utc=occurrence,
                        task_id=task.task_id if hasattr(task, "task_id") else None,
                    )
                )

        return dispatched

    # ------------------------------------------------------------------
    # Lifecycle mutations (called from the personal.reminder tool)
    # ------------------------------------------------------------------

    async def cancel(self, reminder_id: str) -> ReminderRecord:
        """Cancel a reminder and drop its scheduler trigger."""
        record = await asyncio.to_thread(self.store.cancel_reminder, reminder_id)
        self.drop_trigger(reminder_id)
        return record

    async def delete(self, reminder_id: str) -> bool:
        """Delete a reminder and drop its scheduler trigger."""
        deleted = await asyncio.to_thread(self.store.delete_reminder, reminder_id)
        self.drop_trigger(reminder_id)
        return deleted

    async def sync_from_store(self, reminder_id: str) -> ReminderRecord | None:
        """Reload a reminder from the store and resync its trigger."""
        record = await asyncio.to_thread(self.store.get_reminder, reminder_id)
        if record is None:
            self.drop_trigger(reminder_id)
            return None
        self.sync_trigger(record)
        return record


__all__ = [
    "ReminderService",
    "build_reminder_trigger",
]
