"""ProactiveScheduler — Bounded proactive behavior and event scheduling.

Adheres to:
- Phase 8 Architecture (Proactive Behavior)
- Hard Safety Invariants:
  1. Quiet Hours (22:00 -> 08:00 overnight window)
  2. Rate Limiting (max 1 task per 30 minutes)
  3. Direct User Preemption
  4. Mandatory Confirmation on mutating operations (ASK_USER)
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from tom.agents.executor import TaskExecutor
from tom.agents.task_manager import TaskManager
from tom.core.context import CancellationToken
from tom.schemas.proactive import (
    CronTrigger,
    IntervalTrigger,
    ProactivePolicyDecision,
    QuietHoursConfig,
    RateLimitConfig,
    SystemEventTrigger,
)
from tom.schemas.task import Task, TaskResult
from tom.telemetry.logging import get_logger

logger = get_logger(__name__, component="agents.proactive")

AnyTrigger = CronTrigger | IntervalTrigger | SystemEventTrigger


class ProactiveScheduler:
    """Coordinates deterministic, policy-bounded proactive tasks."""

    def __init__(
        self,
        task_manager: TaskManager | None = None,
        task_executor: TaskExecutor | None = None,
        quiet_hours: QuietHoursConfig | None = None,
        rate_limit: RateLimitConfig | None = None,
        clock: Callable[[], datetime] | None = None,
        enabled: bool = True,
    ) -> None:
        self.task_manager = task_manager or TaskManager(db_path=":memory:")
        self.task_executor = task_executor
        self.quiet_hours = quiet_hours or QuietHoursConfig()
        self.rate_limit = rate_limit or RateLimitConfig()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.enabled = enabled

        self._triggers: dict[str, AnyTrigger] = {}
        self._last_proactive_time: datetime | None = None
        self._user_active_override: bool = False
        self._active_proactive_token: CancellationToken | None = None
        self._dispatch_lock = asyncio.Lock()

    @property
    def last_proactive_time(self) -> datetime | None:
        """Timestamp of the most recent proactive task dispatch."""
        return self._last_proactive_time

    def register_trigger(self, trigger: AnyTrigger) -> None:
        """Register a cron, interval, or system event trigger."""
        self._triggers[trigger.trigger_id] = trigger
        logger.info(
            "proactive_trigger_registered",
            trigger_id=trigger.trigger_id,
            trigger_name=trigger.name,
        )

    def unregister_trigger(self, trigger_id: str) -> bool:
        """Unregister a trigger by ID."""
        if trigger_id in self._triggers:
            del self._triggers[trigger_id]
            logger.info("proactive_trigger_unregistered", trigger_id=trigger_id)
            return True
        return False

    def get_triggers(self) -> list[AnyTrigger]:
        """Return all registered triggers."""
        return list(self._triggers.values())

    # ------------------------------------------------------------------
    # User Preemption & Active State
    # ------------------------------------------------------------------

    def signal_user_task_started(self, user_task_id: str | None = None) -> None:
        """Signal that direct user interaction has started, preempting proactive work."""
        self._user_active_override = True
        if self._active_proactive_token is not None:
            logger.info("proactive_preempted_by_user", user_task_id=user_task_id)
            self._active_proactive_token.cancel()

    def signal_user_task_ended(self, user_task_id: str | None = None) -> None:
        """Signal that direct user interaction has concluded."""
        self._user_active_override = False
        logger.info("user_task_ended", user_task_id=user_task_id)

    def is_user_active(self) -> bool:
        """Check if any user task is currently active or user override is set."""
        if self._user_active_override:
            return True

        # Inspect task_manager for non-proactive active tasks
        active_tasks = self.task_manager.list_active()
        for t in active_tasks:
            if not t.metadata.get("is_proactive"):
                return True

        return False

    # ------------------------------------------------------------------
    # Decision Pipeline
    # ------------------------------------------------------------------

    def evaluate_policy(
        self, trigger: AnyTrigger, now: datetime | None = None
    ) -> ProactivePolicyDecision:
        """Evaluate the explicit decision order for proactive execution:

        1. Is scheduler enabled?
        2. Is there an active user task?
        3. Are quiet hours active?
        4. Has the proactive rate limit expired?
        5. Is the trigger valid and due?
        """
        current_time = now or self.clock()

        # 1. Enabled check
        if not self.enabled:
            return ProactivePolicyDecision(
                allowed=False,
                reason="SCHEDULER_DISABLED",
                trigger_id=trigger.trigger_id,
                evaluated_at=current_time,
            )

        # 2. User preemption check
        if self.is_user_active():
            return ProactivePolicyDecision(
                allowed=False,
                reason="USER_TASK_ACTIVE",
                trigger_id=trigger.trigger_id,
                evaluated_at=current_time,
            )

        # 3. Quiet hours check (bypassed if urgent_health is True)
        if self.quiet_hours.is_quiet_hours(current_time) and not trigger.urgent_health:
            return ProactivePolicyDecision(
                allowed=False,
                reason="QUIET_HOURS_ACTIVE",
                trigger_id=trigger.trigger_id,
                evaluated_at=current_time,
            )

        # 4. Proactive rate limit check
        if self._last_proactive_time is not None:
            elapsed = (current_time - self._last_proactive_time).total_seconds()
            if elapsed < self.rate_limit.cooldown_seconds:
                return ProactivePolicyDecision(
                    allowed=False,
                    reason="RATE_LIMIT_COOLDOWN",
                    trigger_id=trigger.trigger_id,
                    evaluated_at=current_time,
                    metadata={
                        "elapsed_seconds": elapsed,
                        "cooldown_seconds": self.rate_limit.cooldown_seconds,
                    },
                )

        # 5. Trigger due check
        if not trigger.is_due(current_time):
            return ProactivePolicyDecision(
                allowed=False,
                reason="TRIGGER_NOT_DUE",
                trigger_id=trigger.trigger_id,
                evaluated_at=current_time,
            )

        return ProactivePolicyDecision(
            allowed=True,
            reason="ALLOWED",
            trigger_id=trigger.trigger_id,
            evaluated_at=current_time,
        )

    # ------------------------------------------------------------------
    # Dispatch & Execution
    # ------------------------------------------------------------------

    async def dispatch_proactive_task(
        self, trigger: AnyTrigger, now: datetime | None = None
    ) -> TaskResult | Task | None:
        """Safely dispatch a proactive task through TaskManager and TaskExecutor."""
        current_time = now or self.clock()

        async with self._dispatch_lock:
            decision = self.evaluate_policy(trigger, current_time)
            if not decision.allowed:
                logger.info(
                    "proactive_dispatch_blocked",
                    trigger_id=trigger.trigger_id,
                    reason=decision.reason,
                )
                return None

            # Mark rate limit timestamp and trigger execution time
            self._last_proactive_time = current_time
            trigger.last_run = current_time

            task = self.task_manager.create(
                goal=trigger.goal,
                metadata={
                    "is_proactive": True,
                    "trigger_id": trigger.trigger_id,
                    "trigger_name": trigger.name,
                },
            )
            token = self.task_manager.get_token(task.task_id)
            self._active_proactive_token = token

        logger.info(
            "proactive_task_dispatched",
            task_id=task.task_id,
            trigger_id=trigger.trigger_id,
            goal=trigger.goal,
        )

        try:
            if self.task_executor is not None and trigger.plan is not None:
                return await self.task_executor.execute(
                    plan=trigger.plan,
                    task_id=task.task_id,
                    cancellation_token=token,
                )
            return task
        finally:
            if self._active_proactive_token is token:
                self._active_proactive_token = None

    async def tick(self, now: datetime | None = None) -> list[TaskResult | Task]:
        """Periodic clock tick evaluating all registered cron and interval triggers."""
        current_time = now or self.clock()
        dispatched: list[TaskResult | Task] = []

        for trigger in list(self._triggers.values()):
            if isinstance(trigger, (CronTrigger, IntervalTrigger)):
                res = await self.dispatch_proactive_task(trigger, current_time)
                if res is not None:
                    dispatched.append(res)
                    # Global rate limit prevents multiple dispatches in the same tick
                    break

        return dispatched

    async def handle_system_event(
        self,
        event_name: str,
        payload: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> TaskResult | Task | None:
        """Handle a system event and dispatch matching proactive triggers."""
        current_time = now or self.clock()

        for trigger in list(self._triggers.values()):
            if isinstance(trigger, SystemEventTrigger) and trigger.event_name == event_name:
                return await self.dispatch_proactive_task(trigger, current_time)

        return None
