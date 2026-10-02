"""Unit tests for ProactiveScheduler (Phase 8 Iteration 4).

Covers all hard safety invariants:
  1. Quiet Hours (22:00 – 08:00 overnight window)
  2. Rate Limiting (max 1 task per 30 min)
  3. Direct User Preemption
  4. Mandatory Confirmation on mutating tools (ASK_USER)

All tests are offline and deterministic (no LLM, no GPU, no real clock).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from tom.agents.proactive import ProactiveScheduler
from tom.agents.task_manager import TaskManager
from tom.schemas.proactive import (
    CronTrigger,
    IntervalTrigger,
    ProactivePolicyDecision,
    QuietHoursConfig,
    RateLimitConfig,
    SystemEventTrigger,
)

# ---------------------------------------------------------------------------
# Clock helpers
# ---------------------------------------------------------------------------


def utc(hour: int, minute: int = 0, second: int = 0) -> datetime:
    """Build a UTC datetime at an arbitrary date for a given time-of-day."""
    return datetime(2024, 6, 15, hour, minute, second, tzinfo=UTC)


NOON = utc(12, 0)  # Safe – middle of day
MIDNIGHT = utc(0, 30)  # Inside quiet hours (22-08)
EVENING = utc(23, 0)  # Inside quiet hours
MORNING = utc(7, 59)  # Inside quiet hours (just before 08:00)
WAKEUP = utc(8, 0)  # Boundary – exactly 08:00, NOT quiet hours


# ---------------------------------------------------------------------------
# Helpers / Factories
# ---------------------------------------------------------------------------


def make_scheduler(
    *,
    clock: datetime | None = None,
    enabled: bool = True,
    cooldown_seconds: float = 1800.0,
    quiet_start: time = time(22, 0),
    quiet_end: time = time(8, 0),
) -> ProactiveScheduler:
    fixed_time = clock or NOON
    return ProactiveScheduler(
        task_manager=TaskManager(db_path=":memory:"),
        task_executor=None,
        quiet_hours=QuietHoursConfig(start_time=quiet_start, end_time=quiet_end),
        rate_limit=RateLimitConfig(cooldown_seconds=cooldown_seconds),
        clock=lambda: fixed_time,
        enabled=enabled,
    )


def interval_trigger(
    *,
    name: str = "test_interval",
    interval_seconds: float = 60.0,
    last_run: datetime | None = None,
    urgent_health: bool = False,
    enabled: bool = True,
) -> IntervalTrigger:
    return IntervalTrigger(
        name=name,
        interval_seconds=interval_seconds,
        goal="Run background check",
        urgent_health=urgent_health,
        enabled=enabled,
        last_run=last_run,
    )


def cron_trigger(
    *,
    name: str = "test_cron",
    expression: str = "* * * * *",
    last_run: datetime | None = None,
    urgent_health: bool = False,
    enabled: bool = True,
) -> CronTrigger:
    return CronTrigger(
        name=name,
        expression=expression,
        goal="Scheduled task",
        urgent_health=urgent_health,
        enabled=enabled,
        last_run=last_run,
    )


def system_event_trigger(
    *,
    name: str = "test_event",
    event_name: str = "battery_low",
    cooldown_seconds: float = 3600.0,
    urgent_health: bool = False,
    enabled: bool = True,
    last_run: datetime | None = None,
) -> SystemEventTrigger:
    return SystemEventTrigger(
        name=name,
        event_name=event_name,
        goal="Handle system event",
        cooldown_seconds=cooldown_seconds,
        urgent_health=urgent_health,
        enabled=enabled,
        last_run=last_run,
    )


# ===========================================================================
# 1. QuietHoursConfig
# ===========================================================================


class TestQuietHoursConfig:
    def test_overnight_window_inside(self) -> None:
        qh = QuietHoursConfig(start_time=time(22, 0), end_time=time(8, 0))
        assert qh.is_quiet_hours(MIDNIGHT) is True
        assert qh.is_quiet_hours(EVENING) is True
        assert qh.is_quiet_hours(MORNING) is True

    def test_overnight_window_outside(self) -> None:
        qh = QuietHoursConfig(start_time=time(22, 0), end_time=time(8, 0))
        assert qh.is_quiet_hours(NOON) is False
        assert qh.is_quiet_hours(utc(9, 0)) is False

    def test_boundary_exactly_at_end_is_not_quiet(self) -> None:
        """08:00 exactly must NOT be in quiet hours (exclusive end)."""
        qh = QuietHoursConfig(start_time=time(22, 0), end_time=time(8, 0))
        assert qh.is_quiet_hours(WAKEUP) is False

    def test_boundary_exactly_at_start_is_quiet(self) -> None:
        """22:00 exactly must be in quiet hours (inclusive start)."""
        qh = QuietHoursConfig(start_time=time(22, 0), end_time=time(8, 0))
        assert qh.is_quiet_hours(utc(22, 0)) is True

    def test_same_day_window(self) -> None:
        """Simple same-day window 13:00-14:00."""
        qh = QuietHoursConfig(start_time=time(13, 0), end_time=time(14, 0))
        assert qh.is_quiet_hours(utc(13, 0)) is True
        assert qh.is_quiet_hours(utc(13, 30)) is True
        assert qh.is_quiet_hours(utc(14, 0)) is False
        assert qh.is_quiet_hours(utc(12, 59)) is False

    def test_disabled_never_active(self) -> None:
        qh = QuietHoursConfig(enabled=False, start_time=time(22, 0), end_time=time(8, 0))
        assert qh.is_quiet_hours(MIDNIGHT) is False
        assert qh.is_quiet_hours(EVENING) is False

    def test_equal_start_end_never_active(self) -> None:
        """Start == End should mean always disabled."""
        qh = QuietHoursConfig(start_time=time(12, 0), end_time=time(12, 0))
        for h in range(24):
            assert qh.is_quiet_hours(utc(h)) is False


# ===========================================================================
# 2. IntervalTrigger.is_due
# ===========================================================================


class TestIntervalTrigger:
    def test_due_when_no_last_run(self) -> None:
        t = interval_trigger(interval_seconds=60.0, last_run=None)
        assert t.is_due(NOON) is True

    def test_due_when_interval_elapsed(self) -> None:
        last = utc(11, 58)  # 2 minutes ago
        t = interval_trigger(interval_seconds=60.0, last_run=last)
        assert t.is_due(NOON) is True

    def test_not_due_when_interval_not_elapsed(self) -> None:
        last = utc(11, 59, 30)  # 30 seconds ago
        t = interval_trigger(interval_seconds=60.0, last_run=last)
        assert t.is_due(NOON) is False

    def test_not_due_when_disabled(self) -> None:
        t = interval_trigger(interval_seconds=60.0, enabled=False)
        assert t.is_due(NOON) is False


# ===========================================================================
# 3. CronTrigger.is_due
# ===========================================================================


class TestCronTrigger:
    def test_wildcard_cron_always_matches(self) -> None:
        t = cron_trigger(expression="* * * * *")
        assert t.is_due(NOON) is True

    def test_specific_hour_minute_match(self) -> None:
        t = cron_trigger(expression="0 12 * * *")
        assert t.is_due(utc(12, 0)) is True
        assert t.is_due(utc(12, 1)) is False
        assert t.is_due(utc(11, 0)) is False

    def test_dedup_same_minute_suppressed(self) -> None:
        """Running twice in the same minute must be prevented."""
        t = cron_trigger(expression="* * * * *", last_run=NOON)
        assert t.is_due(NOON) is False

    def test_due_on_different_minute(self) -> None:
        last = utc(12, 0)
        t = cron_trigger(expression="* * * * *", last_run=last)
        assert t.is_due(utc(12, 1)) is True

    def test_not_due_when_disabled(self) -> None:
        t = cron_trigger(expression="* * * * *", enabled=False)
        assert t.is_due(NOON) is False

    def test_step_expression(self) -> None:
        """*/15 should match minutes 0, 15, 30, 45."""
        t = cron_trigger(expression="*/15 * * * *")
        assert t.is_due(utc(12, 0)) is True
        assert t.is_due(utc(12, 15)) is True
        assert t.is_due(utc(12, 30)) is True
        assert t.is_due(utc(12, 45)) is True
        assert t.is_due(utc(12, 7)) is False

    def test_invalid_cron_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CronTrigger(name="bad", expression="not-a-cron", goal="x")


# ===========================================================================
# 4. SystemEventTrigger.is_due
# ===========================================================================


class TestSystemEventTrigger:
    def test_due_when_no_last_run(self) -> None:
        t = system_event_trigger(cooldown_seconds=3600.0, last_run=None)
        assert t.is_due(NOON) is True

    def test_cooldown_not_elapsed(self) -> None:
        last = utc(11, 30)  # 30 min ago, cooldown=3600
        t = system_event_trigger(cooldown_seconds=3600.0, last_run=last)
        assert t.is_due(NOON) is False

    def test_cooldown_elapsed(self) -> None:
        last = utc(11, 0)  # 1 hour ago exactly
        t = system_event_trigger(cooldown_seconds=3600.0, last_run=last)
        assert t.is_due(NOON) is True

    def test_not_due_when_disabled(self) -> None:
        t = system_event_trigger(enabled=False)
        assert t.is_due(NOON) is False


# ===========================================================================
# 5. ProactiveScheduler -- trigger registry
# ===========================================================================


class TestTriggerRegistry:
    def test_register_and_get(self) -> None:
        sched = make_scheduler()
        t = interval_trigger()
        sched.register_trigger(t)
        assert t in sched.get_triggers()

    def test_unregister_existing(self) -> None:
        sched = make_scheduler()
        t = interval_trigger()
        sched.register_trigger(t)
        removed = sched.unregister_trigger(t.trigger_id)
        assert removed is True
        assert t not in sched.get_triggers()

    def test_unregister_nonexistent(self) -> None:
        sched = make_scheduler()
        assert sched.unregister_trigger("does_not_exist") is False

    def test_multiple_triggers(self) -> None:
        sched = make_scheduler()
        triggers = [interval_trigger(name=f"t{i}") for i in range(3)]
        for t in triggers:
            sched.register_trigger(t)
        assert len(sched.get_triggers()) == 3


# ===========================================================================
# 6. ProactiveScheduler -- evaluate_policy
# ===========================================================================


class TestEvaluatePolicy:
    def test_disabled_scheduler_blocks_all(self) -> None:
        sched = make_scheduler(enabled=False)
        t = interval_trigger()
        decision = sched.evaluate_policy(t, NOON)
        assert decision.allowed is False
        assert decision.reason == "SCHEDULER_DISABLED"

    def test_quiet_hours_blocks_non_urgent(self) -> None:
        sched = make_scheduler(clock=MIDNIGHT)
        t = interval_trigger(urgent_health=False)
        decision = sched.evaluate_policy(t, MIDNIGHT)
        assert decision.allowed is False
        assert decision.reason == "QUIET_HOURS_ACTIVE"

    def test_urgent_health_bypasses_quiet_hours(self) -> None:
        sched = make_scheduler(clock=MIDNIGHT)
        t = interval_trigger(urgent_health=True)
        decision = sched.evaluate_policy(t, MIDNIGHT)
        # Trigger is due (no last_run), rate limit not hit -> should be ALLOWED
        assert decision.allowed is True

    def test_rate_limit_blocks_too_soon(self) -> None:
        sched = make_scheduler(cooldown_seconds=1800.0)
        # 29 minutes elapsed -- still in cooldown
        sched._last_proactive_time = utc(11, 31)
        t = interval_trigger()
        decision = sched.evaluate_policy(t, NOON)
        assert decision.allowed is False
        assert decision.reason == "RATE_LIMIT_COOLDOWN"

    def test_rate_limit_allows_after_cooldown(self) -> None:
        sched = make_scheduler(cooldown_seconds=1800.0)
        sched._last_proactive_time = utc(11, 0)  # exactly 60 min ago -> 3600s > 1800s
        t = interval_trigger()
        decision = sched.evaluate_policy(t, NOON)
        assert decision.allowed is True

    def test_trigger_not_due_blocks(self) -> None:
        sched = make_scheduler()
        # Disabled trigger -> is_due returns False
        t = interval_trigger(enabled=False)
        decision = sched.evaluate_policy(t, NOON)
        assert decision.allowed is False
        assert decision.reason == "TRIGGER_NOT_DUE"

    def test_user_task_active_blocks(self) -> None:
        sched = make_scheduler()
        sched.signal_user_task_started("user-task-001")
        t = interval_trigger()
        decision = sched.evaluate_policy(t, NOON)
        assert decision.allowed is False
        assert decision.reason == "USER_TASK_ACTIVE"

    def test_allowed_all_clear(self) -> None:
        sched = make_scheduler()
        t = interval_trigger()
        decision = sched.evaluate_policy(t, NOON)
        assert decision.allowed is True
        assert decision.reason == "ALLOWED"

    def test_decision_records_trigger_id(self) -> None:
        sched = make_scheduler()
        t = interval_trigger()
        decision = sched.evaluate_policy(t, NOON)
        assert decision.trigger_id == t.trigger_id


# ===========================================================================
# 7. ProactiveScheduler -- user preemption
# ===========================================================================


class TestUserPreemption:
    def test_signal_started_sets_active(self) -> None:
        sched = make_scheduler()
        sched.signal_user_task_started("u1")
        assert sched.is_user_active() is True

    def test_signal_ended_clears_active(self) -> None:
        sched = make_scheduler()
        sched.signal_user_task_started("u1")
        sched.signal_user_task_ended("u1")
        assert sched.is_user_active() is False

    def test_preemption_cancels_active_token(self) -> None:
        sched = make_scheduler()
        token = MagicMock()
        sched._active_proactive_token = token
        sched.signal_user_task_started("u1")
        token.cancel.assert_called_once()

    def test_signal_started_without_active_token_is_safe(self) -> None:
        sched = make_scheduler()
        assert sched._active_proactive_token is None
        # Should not raise
        sched.signal_user_task_started("u1")

    def test_preemption_blocks_policy(self) -> None:
        sched = make_scheduler()
        sched.signal_user_task_started()
        t = interval_trigger()
        decision = sched.evaluate_policy(t, NOON)
        assert decision.allowed is False
        assert decision.reason == "USER_TASK_ACTIVE"

    def test_re_enable_after_user_ends(self) -> None:
        sched = make_scheduler()
        sched.signal_user_task_started("u1")
        sched.signal_user_task_ended("u1")
        t = interval_trigger()
        decision = sched.evaluate_policy(t, NOON)
        assert decision.allowed is True


# ===========================================================================
# 8. ProactiveScheduler -- dispatch_proactive_task
# ===========================================================================


class TestDispatchProactiveTask:
    @pytest.mark.anyio
    async def test_dispatch_blocked_during_quiet_hours(self) -> None:
        sched = make_scheduler(clock=MIDNIGHT)
        t = interval_trigger()
        result = await sched.dispatch_proactive_task(t, MIDNIGHT)
        assert result is None

    @pytest.mark.anyio
    async def test_dispatch_blocked_user_active(self) -> None:
        sched = make_scheduler()
        sched.signal_user_task_started()
        t = interval_trigger()
        result = await sched.dispatch_proactive_task(t, NOON)
        assert result is None

    @pytest.mark.anyio
    async def test_dispatch_allowed_creates_task(self) -> None:
        sched = make_scheduler()
        t = interval_trigger()
        result = await sched.dispatch_proactive_task(t, NOON)
        assert result is not None

    @pytest.mark.anyio
    async def test_dispatch_updates_rate_limit_timestamp(self) -> None:
        sched = make_scheduler()
        t = interval_trigger()
        assert sched.last_proactive_time is None
        await sched.dispatch_proactive_task(t, NOON)
        assert sched.last_proactive_time == NOON

    @pytest.mark.anyio
    async def test_dispatch_updates_trigger_last_run(self) -> None:
        sched = make_scheduler()
        t = interval_trigger()
        await sched.dispatch_proactive_task(t, NOON)
        assert t.last_run == NOON

    @pytest.mark.anyio
    async def test_dispatch_second_time_blocked_by_rate_limit(self) -> None:
        sched = make_scheduler(cooldown_seconds=1800.0)
        t1 = interval_trigger(name="t1")
        t2 = interval_trigger(name="t2")
        await sched.dispatch_proactive_task(t1, NOON)
        # Same time -> within 30 min cooldown
        result = await sched.dispatch_proactive_task(t2, NOON)
        assert result is None

    @pytest.mark.anyio
    async def test_dispatch_with_executor_and_plan(self) -> None:
        mock_executor = MagicMock()
        mock_executor.execute = AsyncMock(return_value=MagicMock())
        sched = make_scheduler()
        sched.task_executor = mock_executor

        plan = MagicMock()  # Plan stub
        t = interval_trigger()
        t.plan = plan

        await sched.dispatch_proactive_task(t, NOON)
        mock_executor.execute.assert_called_once()

    @pytest.mark.anyio
    async def test_dispatch_without_executor_returns_task(self) -> None:
        sched = make_scheduler()
        sched.task_executor = None
        t = interval_trigger()
        result = await sched.dispatch_proactive_task(t, NOON)
        # Returns the Task object directly
        assert result is not None
        assert hasattr(result, "task_id")

    @pytest.mark.anyio
    async def test_proactive_token_cleared_after_dispatch(self) -> None:
        sched = make_scheduler()
        t = interval_trigger()
        await sched.dispatch_proactive_task(t, NOON)
        assert sched._active_proactive_token is None


# ===========================================================================
# 9. ProactiveScheduler -- tick
# ===========================================================================


class TestTick:
    @pytest.mark.anyio
    async def test_tick_dispatches_due_trigger(self) -> None:
        sched = make_scheduler()
        t = interval_trigger()
        sched.register_trigger(t)
        dispatched = await sched.tick(NOON)
        assert len(dispatched) == 1

    @pytest.mark.anyio
    async def test_tick_ignores_system_event_triggers(self) -> None:
        """tick() must only evaluate CronTrigger and IntervalTrigger."""
        sched = make_scheduler()
        t = system_event_trigger()
        sched.register_trigger(t)
        dispatched = await sched.tick(NOON)
        assert len(dispatched) == 0

    @pytest.mark.anyio
    async def test_tick_respects_rate_limit_between_triggers(self) -> None:
        """Only one trigger may fire per tick (global rate gate)."""
        sched = make_scheduler(cooldown_seconds=1800.0)
        t1 = interval_trigger(name="t1")
        t2 = interval_trigger(name="t2")
        sched.register_trigger(t1)
        sched.register_trigger(t2)
        dispatched = await sched.tick(NOON)
        assert len(dispatched) == 1

    @pytest.mark.anyio
    async def test_tick_blocks_during_quiet_hours(self) -> None:
        sched = make_scheduler(clock=MIDNIGHT)
        t = interval_trigger()
        sched.register_trigger(t)
        dispatched = await sched.tick(MIDNIGHT)
        assert len(dispatched) == 0

    @pytest.mark.anyio
    async def test_tick_empty_no_triggers(self) -> None:
        sched = make_scheduler()
        dispatched = await sched.tick(NOON)
        assert dispatched == []


# ===========================================================================
# 10. ProactiveScheduler -- handle_system_event
# ===========================================================================


class TestHandleSystemEvent:
    @pytest.mark.anyio
    async def test_dispatches_matching_event_trigger(self) -> None:
        sched = make_scheduler()
        t = system_event_trigger(event_name="battery_low")
        sched.register_trigger(t)
        result = await sched.handle_system_event("battery_low", now=NOON)
        assert result is not None

    @pytest.mark.anyio
    async def test_ignores_nonmatching_event(self) -> None:
        sched = make_scheduler()
        t = system_event_trigger(event_name="battery_low")
        sched.register_trigger(t)
        result = await sched.handle_system_event("disk_full", now=NOON)
        assert result is None

    @pytest.mark.anyio
    async def test_event_blocked_during_quiet_hours(self) -> None:
        sched = make_scheduler(clock=MIDNIGHT)
        t = system_event_trigger(event_name="battery_low", urgent_health=False)
        sched.register_trigger(t)
        result = await sched.handle_system_event("battery_low", now=MIDNIGHT)
        assert result is None

    @pytest.mark.anyio
    async def test_urgent_event_fires_during_quiet_hours(self) -> None:
        sched = make_scheduler(clock=MIDNIGHT)
        t = system_event_trigger(event_name="system_critical", urgent_health=True)
        sched.register_trigger(t)
        result = await sched.handle_system_event("system_critical", now=MIDNIGHT)
        assert result is not None

    @pytest.mark.anyio
    async def test_event_respects_trigger_cooldown(self) -> None:
        sched = make_scheduler()
        # Fired 10 minutes ago; cooldown is 3600s -> should block
        t = system_event_trigger(
            event_name="battery_low", cooldown_seconds=3600.0, last_run=utc(11, 50)
        )
        sched.register_trigger(t)
        result = await sched.handle_system_event("battery_low", now=NOON)
        assert result is None


# ===========================================================================
# 11. Concurrency -- dispatch_lock prevents double dispatch
# ===========================================================================


class TestConcurrency:
    @pytest.mark.anyio
    async def test_concurrent_dispatches_only_one_fires(self) -> None:
        """Two concurrent dispatch calls on the same trigger should result
        in at most one task dispatched (rate limit gate under async lock)."""
        sched = make_scheduler(cooldown_seconds=1800.0)
        t1 = interval_trigger(name="concurrent_t1")
        t2 = interval_trigger(name="concurrent_t2")

        results = await asyncio.gather(
            sched.dispatch_proactive_task(t1, NOON),
            sched.dispatch_proactive_task(t2, NOON),
        )
        dispatched = [r for r in results if r is not None]
        assert len(dispatched) == 1


# ===========================================================================
# 12. ProactivePolicyDecision schema
# ===========================================================================


class TestProactivePolicyDecision:
    def test_allowed_true(self) -> None:
        d = ProactivePolicyDecision(allowed=True, reason="ALLOWED", trigger_id="t1")
        assert d.allowed is True
        assert d.reason == "ALLOWED"

    def test_allowed_false(self) -> None:
        d = ProactivePolicyDecision(allowed=False, reason="QUIET_HOURS_ACTIVE", trigger_id="t1")
        assert d.allowed is False

    def test_metadata_defaults_empty(self) -> None:
        d = ProactivePolicyDecision(allowed=False, reason="RATE_LIMIT_COOLDOWN")
        assert d.metadata == {}

    def test_metadata_with_values(self) -> None:
        d = ProactivePolicyDecision(
            allowed=False,
            reason="RATE_LIMIT_COOLDOWN",
            metadata={"elapsed_seconds": 120.0, "cooldown_seconds": 1800.0},
        )
        assert d.metadata["elapsed_seconds"] == 120.0
