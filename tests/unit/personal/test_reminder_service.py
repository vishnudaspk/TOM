"""Unit tests for ReminderService — ProactiveScheduler integration (Phase 10 Iteration 4).

Covers scheduler-safe lifecycle behavior:
- cancellation, duplicate suppression, bounded recurrence
- quiet-hours / user preemption / rate-limit behavior (existing scheduler policy)
- no arbitrary task execution (plan-less dispatch only)

All tests are offline and deterministic (no LLM, no GPU, no wall-clock dependency).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tom.agents.proactive import ProactiveScheduler
from tom.personal.reminders import ReminderService, build_reminder_trigger
from tom.personal.store import PersonalStore
from tom.schemas.personal import ReminderRecurrence, ReminderStatus
from tom.schemas.proactive import QuietHoursConfig, RateLimitConfig


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def make_scheduler(
    *,
    quiet_hours_enabled: bool = False,
    cooldown_seconds: float = 0.0,
    enabled: bool = True,
) -> ProactiveScheduler:
    """Neutral scheduler for deterministic tests (policy toggles explicit)."""
    return ProactiveScheduler(
        quiet_hours=QuietHoursConfig(enabled=quiet_hours_enabled),
        rate_limit=RateLimitConfig(cooldown_seconds=cooldown_seconds),
        enabled=enabled,
    )


@pytest.fixture()
def store(tmp_path: Path):
    s = PersonalStore(db_path=str(tmp_path / "reminders.db"))
    yield s
    s.close()


@pytest.fixture()
def service(store: PersonalStore) -> ReminderService:
    return ReminderService(store, scheduler=make_scheduler())


# ---------------------------------------------------------------------------
# Trigger construction & due alignment
# ---------------------------------------------------------------------------


class TestBuildReminderTrigger:
    def test_never_fired_trigger_shape(self, store: PersonalStore) -> None:
        r = store.create_reminder("stand up", _utc(2026, 10, 10, 9))
        trigger = build_reminder_trigger(r)
        assert trigger.trigger_id == f"reminder_{r.reminder_id}"
        assert trigger.plan is None, "reminders must never carry executable plans"
        assert trigger.urgent_health is False, "reminders must respect quiet hours"
        assert trigger.last_run is None
        assert trigger.is_due(_utc(2026, 10, 10, 9)) is True

    def test_fired_trigger_due_exactly_at_next_fire(self, store: PersonalStore) -> None:
        r = store.create_reminder("d", _utc(2026, 10, 10, 9), ReminderRecurrence.DAILY)
        store.record_reminder_fire(r.reminder_id, 0, _utc(2026, 10, 10, 9))
        fired = store.get_reminder(r.reminder_id)
        assert fired is not None
        trigger = build_reminder_trigger(fired)
        # is_due must be false one second before and true at the next fire.
        assert trigger.is_due(_utc(2026, 10, 11, 8, 59, 59)) is False
        assert trigger.is_due(_utc(2026, 10, 11, 9)) is True

    def test_inactive_reminder_drops_trigger(self, store: PersonalStore) -> None:
        r = store.create_reminder("c", _utc(2026, 10, 10, 9))
        cancelled = store.cancel_reminder(r.reminder_id)
        service = ReminderService(store, scheduler=make_scheduler())
        assert service.sync_trigger(cancelled) is None
        assert service.get_trigger(r.reminder_id) is None


# ---------------------------------------------------------------------------
# Dispatch through the scheduler
# ---------------------------------------------------------------------------


class TestTickDispatch:
    pytestmark = pytest.mark.asyncio

    async def test_due_one_shot_fires_once_and_completes(
        self, store: PersonalStore, service: ReminderService
    ) -> None:
        r = store.create_reminder("water", _utc(2026, 10, 10, 9))
        dispatched = await service.tick(_utc(2026, 10, 10, 9))
        assert len(dispatched) == 1
        assert dispatched[0].reminder_id == r.reminder_id
        assert dispatched[0].message == "water"
        assert dispatched[0].occurrence_utc == _utc(2026, 10, 10, 9)
        assert dispatched[0].task_id is not None

        final = store.get_reminder(r.reminder_id)
        assert final is not None
        assert final.fires == 1
        assert final.status == ReminderStatus.COMPLETED
        # Completed reminder: its local trigger is dropped.
        assert service.get_trigger(r.reminder_id) is None

    async def test_not_due_reminder_does_not_fire(
        self, store: PersonalStore, service: ReminderService
    ) -> None:
        store.create_reminder("later", _utc(2026, 10, 10, 9))
        assert await service.tick(_utc(2026, 10, 10, 8, 59)) == []

    async def test_duplicate_tick_same_occurrence_fires_once(
        self, store: PersonalStore, service: ReminderService
    ) -> None:
        r = store.create_reminder("once", _utc(2026, 10, 10, 9))
        first = await service.tick(_utc(2026, 10, 10, 9))
        second = await service.tick(_utc(2026, 10, 10, 9))
        assert len(first) == 1
        assert second == []
        assert store.get_reminder(r.reminder_id).fires == 1  # type: ignore[union-attr]

    async def test_scheduler_records_proactive_task_without_plan_execution(
        self, store: PersonalStore, service: ReminderService
    ) -> None:
        store.create_reminder("audit", _utc(2026, 10, 10, 9))
        await service.tick(_utc(2026, 10, 10, 9))
        tasks = service.scheduler.task_manager.list_all()
        assert len(tasks) == 1
        assert tasks[0].metadata.get("is_proactive") is True
        assert tasks[0].goal.startswith("REMINDER:")

    async def test_recurring_fires_each_interval_until_budget(
        self, store: PersonalStore, service: ReminderService
    ) -> None:
        r = store.create_reminder(
            "daily", _utc(2026, 10, 10, 9), ReminderRecurrence.DAILY, max_fires=3
        )
        now = _utc(2026, 10, 10, 9)
        assert len(await service.tick(now)) == 1
        # Scheduler rate limit is neutral (cooldown 0) for this fixture.
        assert len(await service.tick(now + timedelta(days=1))) == 1
        assert len(await service.tick(now + timedelta(days=2))) == 1
        final = store.get_reminder(r.reminder_id)
        assert final is not None
        assert final.status == ReminderStatus.COMPLETED
        # Bounded recurrence: no fourth fire ever occurs.
        assert await service.tick(now + timedelta(days=30)) == []


# ---------------------------------------------------------------------------
# Existing scheduler policy: quiet hours, preemption, rate limit, enablement
# ---------------------------------------------------------------------------


class TestSchedulerPolicyIntegration:
    pytestmark = pytest.mark.asyncio

    async def test_quiet_hours_defer_then_fire_after_window(self, store: PersonalStore) -> None:
        """Quiet hours (existing policy) defer the reminder without losing it."""
        service = ReminderService(store, scheduler=make_scheduler(quiet_hours_enabled=True))
        r = store.create_reminder("night", _utc(2026, 10, 10, 23, 0))

        # 23:00 falls inside the default 22:00-08:00 quiet window.
        assert await service.tick(_utc(2026, 10, 10, 23, 0)) == []
        deferred = store.get_reminder(r.reminder_id)
        assert deferred is not None
        assert deferred.fires == 0
        assert deferred.status == ReminderStatus.ACTIVE

        # After quiet hours end the still-due reminder fires.
        fired = await service.tick(_utc(2026, 10, 11, 8, 1))
        assert len(fired) == 1
        assert store.get_reminder(r.reminder_id).fires == 1  # type: ignore[union-attr]

    async def test_user_preemption_blocks_then_allows(self, store: PersonalStore) -> None:
        service = ReminderService(store, scheduler=make_scheduler())
        r = store.create_reminder("preempted", _utc(2026, 10, 10, 9))

        service.scheduler.signal_user_task_started("user-task")
        assert await service.tick(_utc(2026, 10, 10, 9)) == []
        assert store.get_reminder(r.reminder_id).fires == 0  # type: ignore[union-attr]

        service.scheduler.signal_user_task_ended("user-task")
        assert len(await service.tick(_utc(2026, 10, 10, 9))) == 1

    async def test_rate_limit_defers_second_reminder(self, store: PersonalStore) -> None:
        """Default 30-minute proactive cooldown throttles multiple due reminders."""
        service = ReminderService(store, scheduler=make_scheduler(cooldown_seconds=1800.0))
        store.create_reminder("first", _utc(2026, 10, 10, 9))
        store.create_reminder("second", _utc(2026, 10, 10, 9, 1))

        fired = await service.tick(_utc(2026, 10, 10, 9, 1))
        assert len(fired) == 1  # only one dispatch per cooldown window
        assert fired[0].message == "first"

        # The deferred reminder remains active and due for a later tick.
        statuses = {r.message: r for r in store.list_reminders()}
        assert statuses["second"].fires == 0
        assert statuses["second"].status == ReminderStatus.ACTIVE

        # After the cooldown expires the second reminder fires.
        later = await service.tick(_utc(2026, 10, 10, 9, 31))
        assert len(later) == 1
        assert later[0].message == "second"

    async def test_disabled_scheduler_defers_all(self, store: PersonalStore) -> None:
        service = ReminderService(store, scheduler=make_scheduler(enabled=False))
        store.create_reminder("off", _utc(2026, 10, 10, 9))
        assert await service.tick(_utc(2026, 10, 10, 9)) == []
        assert store.list_reminders()[0].fires == 0

    async def test_neutral_scheduler_fires_both_same_tick(self, store: PersonalStore) -> None:
        service = ReminderService(store, scheduler=make_scheduler())
        store.create_reminder("a", _utc(2026, 10, 10, 9))
        store.create_reminder("b", _utc(2026, 10, 10, 9))
        fired = await service.tick(_utc(2026, 10, 10, 9))
        assert len(fired) == 2


# ---------------------------------------------------------------------------
# Lifecycle: cancellation, deletion, update resync, concurrency
# ---------------------------------------------------------------------------


class TestReminderLifecycle:
    pytestmark = pytest.mark.asyncio

    async def test_cancel_stops_firing_and_drops_trigger(
        self, store: PersonalStore, service: ReminderService
    ) -> None:
        r = store.create_reminder("cancel me", _utc(2026, 10, 10, 9))
        await service.sync_from_store(r.reminder_id)
        assert service.get_trigger(r.reminder_id) is not None

        cancelled = await service.cancel(r.reminder_id)
        assert cancelled.status == ReminderStatus.CANCELLED
        assert service.get_trigger(r.reminder_id) is None
        assert await service.tick(_utc(2026, 10, 10, 9)) == []
        assert store.get_reminder(r.reminder_id).fires == 0  # type: ignore[union-attr]

    async def test_delete_removes_row_and_trigger(
        self, store: PersonalStore, service: ReminderService
    ) -> None:
        r = store.create_reminder("delete me", _utc(2026, 10, 10, 9))
        await service.sync_from_store(r.reminder_id)
        assert await service.delete(r.reminder_id) is True
        assert store.get_reminder(r.reminder_id) is None
        assert service.get_trigger(r.reminder_id) is None
        assert await service.tick(_utc(2026, 10, 10, 9)) == []

    async def test_update_resyncs_trigger_timing(
        self, store: PersonalStore, service: ReminderService
    ) -> None:
        r = store.create_reminder("move", _utc(2026, 10, 10, 9))
        await service.sync_from_store(r.reminder_id)
        store.update_reminder(r.reminder_id, {"fire_utc": _utc(2026, 10, 12, 17)})
        await service.sync_from_store(r.reminder_id)

        trigger = service.get_trigger(r.reminder_id)
        assert trigger is not None
        # Never-fired triggers rely on the service's due gate (fire_utc <= now);
        # is_due itself only becomes the timing source after the first fire.
        assert trigger.is_due(_utc(2026, 10, 12, 17)) is True

        # Old time no longer fires; new time does.
        assert await service.tick(_utc(2026, 10, 10, 9)) == []
        assert len(await service.tick(_utc(2026, 10, 12, 17))) == 1

    async def test_concurrent_ticks_fire_once(
        self, store: PersonalStore, service: ReminderService
    ) -> None:
        """Duplicate suppression holds under concurrent tick calls."""
        r = store.create_reminder("race", _utc(2026, 10, 10, 9))
        now = _utc(2026, 10, 10, 9)
        results = await asyncio.gather(service.tick(now), service.tick(now))
        total = sum(len(part) for part in results)
        assert total == 1
        assert store.get_reminder(r.reminder_id).fires == 1  # type: ignore[union-attr]
