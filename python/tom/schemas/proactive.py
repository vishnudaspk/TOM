"""Pydantic schemas and models for TOM Proactive Behavior & Event Scheduling.

Adheres to:
- Phase 8 Architecture (Proactive Behavior)
- Decision 051: Task Lifecycle State Machine
- Decision 035: Step-Bounded Execution Loop & Cooperative Task Cancellation
- Hard Safety Invariants:
  1. Quiet Hours (default 22:00 -> 08:00 overnight window)
  2. Proactive Rate Limiting (max 1 task per 30 minutes)
  3. Direct User Preemption
  4. Mandatory Confirmation on mutating tools (ASK_USER)
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, time
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tom.schemas.planner import Plan


class TriggerType(StrEnum):
    """Types of proactive trigger sources."""

    CRON = "cron"
    INTERVAL = "interval"
    SYSTEM_EVENT = "system_event"


class QuietHoursConfig(BaseModel):
    """Configuration for quiet hours during which non-urgent proactive tasks are suppressed."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = Field(default=True, description="Whether quiet hours enforcement is active")
    start_time: time = Field(
        default=time(22, 0), description="Start of quiet hours (inclusive, e.g. 22:00)"
    )
    end_time: time = Field(
        default=time(8, 0), description="End of quiet hours (exclusive, e.g. 08:00)"
    )

    def is_quiet_hours(self, target: datetime | time) -> bool:
        """Evaluate if the given target time falls within quiet hours.

        Correctly handles the overnight window across midnight:
        - If start > end (e.g. 22:00 to 08:00): active when t >= start OR t < end.
        - If start < end (e.g. 13:00 to 14:00): active when start <= t < end.
        - If start == end: disabled.
        """
        if not self.enabled:
            return False

        t = target.time() if isinstance(target, datetime) else target

        if self.start_time > self.end_time:
            # Overnight window across midnight (e.g. 22:00 -> 08:00)
            return t >= self.start_time or t < self.end_time
        elif self.start_time < self.end_time:
            # Same-day window
            return self.start_time <= t < self.end_time
        else:
            return False


class RateLimitConfig(BaseModel):
    """Global rate limiting configuration for proactive tasks."""

    model_config = ConfigDict(extra="ignore")

    cooldown_seconds: float = Field(
        default=1800.0,
        ge=0.0,
        description="Minimum seconds between any proactive task executions (default 30 min)",
    )


def _match_cron_field(field_expr: str, value: int, min_val: int, max_val: int) -> bool:
    """Evaluate whether an integer value matches a cron field expression."""
    clean = field_expr.strip()
    if clean == "*":
        return True

    for part in clean.split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith("*/"):
            step = int(part[2:])
            if step <= 0:
                raise ValueError(f"Step must be > 0 in cron expression: {field_expr}")
            if (value - min_val) % step == 0:
                return True
        elif "-" in part:
            subparts = part.split("-")
            if len(subparts) == 2:
                low, high = int(subparts[0]), int(subparts[1])
                if low <= value <= high:
                    return True
        else:
            if int(part) == value:
                return True
    return False


def validate_cron_expression(expr: str) -> None:
    """Validate that a 5-field cron expression is well-formed."""
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(
            f"Cron expression must have exactly 5 fields (minute hour dom month dow), got {len(parts)}: '{expr}'"
        )
    field_ranges = [
        ("minute", 0, 59),
        ("hour", 0, 23),
        ("dom", 1, 31),
        ("month", 1, 12),
        ("dow", 0, 6),
    ]
    for (name, _min_v, _max_v), part in zip(field_ranges, parts, strict=True):
        if part == "*":
            continue
        if not re.match(r"^[\d\*\/\,\-]+$", part):
            raise ValueError(f"Invalid character in cron field '{name}': '{part}'")


class CronTrigger(BaseModel):
    """Cron-scheduled proactive trigger."""

    model_config = ConfigDict(extra="ignore")

    trigger_id: str = Field(default_factory=lambda: f"cron_{uuid.uuid4().hex[:8]}")
    name: str = Field(..., min_length=1)
    expression: str = Field(..., description="5-field cron expression: min hour dom month dow")
    goal: str = Field(..., min_length=1)
    plan: Plan | None = None
    urgent_health: bool = Field(
        default=False, description="Whether this trigger bypasses quiet hours"
    )
    enabled: bool = True
    last_run: datetime | None = None

    @field_validator("expression")
    @classmethod
    def check_cron(cls, v: str) -> str:
        validate_cron_expression(v)
        return v.strip()

    def matches(self, dt: datetime) -> bool:
        """Check if datetime matches the 5-field cron expression."""
        parts = self.expression.split()
        minute, hour, dom, month, dow = parts

        # Python weekday: Mon=0 .. Sun=6 -> Cron standard: Sun=0 .. Sat=6
        cron_dow = (dt.weekday() + 1) % 7

        if not _match_cron_field(minute, dt.minute, 0, 59):
            return False
        if not _match_cron_field(hour, dt.hour, 0, 23):
            return False
        if not _match_cron_field(dom, dt.day, 1, 31):
            return False
        if not _match_cron_field(month, dt.month, 1, 12):
            return False
        if not _match_cron_field(dow, cron_dow, 0, 6):
            return False
        return True

    def is_due(self, dt: datetime) -> bool:
        """Check if trigger is due to fire at datetime."""
        if not self.enabled:
            return False
        if not self.matches(dt):
            return False
        if self.last_run is not None:
            # Same minute de-duplication
            if (
                self.last_run.year == dt.year
                and self.last_run.month == dt.month
                and self.last_run.day == dt.day
                and self.last_run.hour == dt.hour
                and self.last_run.minute == dt.minute
            ):
                return False
        return True


class IntervalTrigger(BaseModel):
    """Fixed duration recurring proactive trigger."""

    model_config = ConfigDict(extra="ignore")

    trigger_id: str = Field(default_factory=lambda: f"interval_{uuid.uuid4().hex[:8]}")
    name: str = Field(..., min_length=1)
    interval_seconds: float = Field(gt=0.0, description="Duration between runs in seconds")
    goal: str = Field(..., min_length=1)
    plan: Plan | None = None
    urgent_health: bool = Field(
        default=False, description="Whether this trigger bypasses quiet hours"
    )
    enabled: bool = True
    last_run: datetime | None = None

    def is_due(self, dt: datetime) -> bool:
        """Check if interval has elapsed since last_run."""
        if not self.enabled:
            return False
        if self.last_run is None:
            return True
        return (dt - self.last_run).total_seconds() >= self.interval_seconds


class SystemEventTrigger(BaseModel):
    """Event-driven proactive trigger activated by system telemetry or events."""

    model_config = ConfigDict(extra="ignore")

    trigger_id: str = Field(default_factory=lambda: f"event_{uuid.uuid4().hex[:8]}")
    name: str = Field(..., min_length=1)
    event_name: str = Field(..., min_length=1, description="Event topic or name to match")
    goal: str = Field(..., min_length=1)
    plan: Plan | None = None
    urgent_health: bool = Field(
        default=False, description="Whether this trigger bypasses quiet hours"
    )
    cooldown_seconds: float = Field(
        default=3600.0, ge=0.0, description="Minimum cooldown between event firings"
    )
    enabled: bool = True
    last_run: datetime | None = None

    def is_due(self, dt: datetime) -> bool:
        """Check if event cooldown has elapsed."""
        if not self.enabled:
            return False
        if self.last_run is None:
            return True
        return (dt - self.last_run).total_seconds() >= self.cooldown_seconds


class ProactivePolicyDecision(BaseModel):
    """Deterministic audit record for a proactive scheduling evaluation."""

    model_config = ConfigDict(extra="ignore")

    allowed: bool
    reason: str
    trigger_id: str | None = None
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)
