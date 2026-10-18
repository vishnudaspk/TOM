"""Pydantic schemas for Phase 10 personal productivity tools.

Adheres to:
- Phase 10 Architecture (personal.note, personal.calendar, personal.reminder)
- skills/coding/validation: strict bounds, timezone-aware datetimes, extra="forbid".
- UTC timestamps and stable identifiers for all persisted personal records.
- Personal records are independent from semantic memory records (separate schemas).

Security boundary:
- These schemas are only reachable through the typed tool pipeline
  (ToolRegistry -> ToolExecutor -> PermissionEngine). Personal data never enters
  MemoryManager records or Qdrant automatically.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Bounded field limits (deterministic, enforced at the boundary)
# ---------------------------------------------------------------------------

NOTE_TITLE_MAX = 200
NOTE_CONTENT_MAX = 20_000
NOTE_TAGS_MAX = 10
NOTE_TAG_LENGTH_MAX = 50
NOTE_QUERY_MAX = 200

EVENT_TITLE_MAX = 200
EVENT_DESCRIPTION_MAX = 2_000

REMINDER_MESSAGE_MAX = 1_000
REMINDER_MAX_FIRES_LIMIT = 1_000
REMINDER_DEFAULT_RECURRING_MAX_FIRES = 100

LIST_LIMIT_MAX = 100
LIST_LIMIT_DEFAULT = 20

_ID_PATTERN = r"^(note|evt|rem)_[0-9a-f]{12}$"


def _require_utc_aware(value: datetime, field_name: str) -> datetime:
    """Reject naive datetimes and normalize aware datetimes to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            f"{field_name} must be an explicit timezone-aware UTC timestamp "
            f"(e.g. 2026-10-09T14:30:00+00:00)"
        )
    return value.astimezone(UTC)


class ReminderRecurrence(StrEnum):
    """Bounded recurrence options for reminders (Phase 10 approved model)."""

    ONE_SHOT = "one_shot"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class ReminderStatus(StrEnum):
    """Lifecycle status of a persisted reminder."""

    ACTIVE = "active"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


# ---------------------------------------------------------------------------
# Persisted record models (output side)
# ---------------------------------------------------------------------------


class PersonalNoteRecord(BaseModel):
    """A single persisted personal note. Timestamps are UTC and timezone-aware."""

    model_config = ConfigDict(extra="forbid")

    note_id: str = Field(min_length=5, max_length=64, pattern=_ID_PATTERN)
    title: str = Field(min_length=1, max_length=NOTE_TITLE_MAX)
    content: str = Field(min_length=1, max_length=NOTE_CONTENT_MAX)
    tags: list[str] = Field(default_factory=list, max_length=NOTE_TAGS_MAX)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def _utc_aware(cls, value: datetime) -> datetime:
        return _require_utc_aware(value, "timestamp")

    @field_validator("tags")
    @classmethod
    def _bounded_tags(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for tag in value:
            tag = tag.strip()
            if not tag or len(tag) > NOTE_TAG_LENGTH_MAX:
                raise ValueError(
                    f"each tag must be 1-{NOTE_TAG_LENGTH_MAX} characters after trimming"
                )
            cleaned.append(tag)
        return cleaned


class CalendarEventRecord(BaseModel):
    """A single persisted calendar event with explicit UTC timestamps."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=5, max_length=64, pattern=_ID_PATTERN)
    title: str = Field(min_length=1, max_length=EVENT_TITLE_MAX)
    description: str = Field(default="", max_length=EVENT_DESCRIPTION_MAX)
    start_utc: datetime
    end_utc: datetime
    created_at: datetime
    updated_at: datetime

    @field_validator("start_utc", "end_utc", "created_at", "updated_at")
    @classmethod
    def _utc_aware(cls, value: datetime) -> datetime:
        return _require_utc_aware(value, "timestamp")

    @model_validator(mode="after")
    def _ordered_window(self) -> CalendarEventRecord:
        if self.end_utc <= self.start_utc:
            raise ValueError("end_utc must be after start_utc")
        return self


class ReminderRecord(BaseModel):
    """A single persisted reminder with bounded recurrence semantics."""

    model_config = ConfigDict(extra="forbid")

    reminder_id: str = Field(min_length=5, max_length=64, pattern=_ID_PATTERN)
    message: str = Field(min_length=1, max_length=REMINDER_MESSAGE_MAX)
    fire_utc: datetime
    recurrence: ReminderRecurrence = ReminderRecurrence.ONE_SHOT
    max_fires: int = Field(ge=1, le=REMINDER_MAX_FIRES_LIMIT)
    fires: int = Field(default=0, ge=0)
    status: ReminderStatus = ReminderStatus.ACTIVE
    last_fired_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("fire_utc", "created_at", "updated_at")
    @classmethod
    def _utc_aware(cls, value: datetime) -> datetime:
        return _require_utc_aware(value, "timestamp")

    @field_validator("last_fired_at")
    @classmethod
    def _utc_aware_optional(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_utc_aware(value, "last_fired_at")


# ---------------------------------------------------------------------------
# Tool input schemas
# ---------------------------------------------------------------------------


class PersonalNoteInput(BaseModel):
    """Validated input for the deterministic `personal.note` tool."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["create", "retrieve", "update", "delete"] = Field(
        description="Operation to perform; 'retrieve' is SAFE, mutations require confirmation"
    )
    note_id: str | None = Field(
        default=None,
        pattern=_ID_PATTERN,
        description="Stable note identifier (required for update/delete; optional for retrieve)",
    )
    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=NOTE_TITLE_MAX,
        description="Note title (required for create)",
    )
    content: str | None = Field(
        default=None,
        min_length=1,
        max_length=NOTE_CONTENT_MAX,
        description="Note body text (required for create)",
    )
    tags: list[str] | None = Field(
        default=None,
        max_length=NOTE_TAGS_MAX,
        description=f"Optional tags (max {NOTE_TAGS_MAX}, each 1-{NOTE_TAG_LENGTH_MAX} chars)",
    )
    query: str | None = Field(
        default=None,
        min_length=1,
        max_length=NOTE_QUERY_MAX,
        description="Optional case-insensitive substring filter for retrieve",
    )
    limit: int = Field(
        default=LIST_LIMIT_DEFAULT,
        ge=1,
        le=LIST_LIMIT_MAX,
        description="Maximum number of notes returned by a list retrieve",
    )

    @model_validator(mode="after")
    def _check_action_fields(self) -> PersonalNoteInput:
        if self.action == "create":
            if not self.title or not self.content:
                raise ValueError("action 'create' requires both title and content")
        elif self.action == "update":
            if not self.note_id:
                raise ValueError("action 'update' requires note_id")
            if self.title is None and self.content is None and self.tags is None:
                raise ValueError("action 'update' requires at least one of title, content, or tags")
        elif self.action == "delete":
            if not self.note_id:
                raise ValueError("action 'delete' requires note_id")
        return self


class PersonalCalendarInput(BaseModel):
    """Validated input for the deterministic `personal.calendar` tool."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["create", "retrieve", "update", "delete"] = Field(
        description="Operation to perform; 'retrieve' is SAFE, mutations require confirmation"
    )
    event_id: str | None = Field(
        default=None,
        pattern=_ID_PATTERN,
        description="Stable event identifier (required for update/delete; optional for retrieve)",
    )
    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=EVENT_TITLE_MAX,
        description="Event title (required for create)",
    )
    description: str | None = Field(
        default=None,
        max_length=EVENT_DESCRIPTION_MAX,
        description="Optional event description",
    )
    start_utc: datetime | None = Field(
        default=None,
        description="Explicit timezone-aware UTC start timestamp (required for create)",
    )
    end_utc: datetime | None = Field(
        default=None,
        description="Explicit timezone-aware UTC end timestamp; must be after start_utc",
    )
    window_start_utc: datetime | None = Field(
        default=None,
        description="Optional timezone-aware UTC lower bound for retrieve",
    )
    window_end_utc: datetime | None = Field(
        default=None,
        description="Optional timezone-aware UTC upper bound for retrieve",
    )
    limit: int = Field(
        default=LIST_LIMIT_DEFAULT,
        ge=1,
        le=LIST_LIMIT_MAX,
        description="Maximum number of events returned by a retrieve",
    )

    @field_validator("start_utc", "end_utc", "window_start_utc", "window_end_utc")
    @classmethod
    def _utc_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_utc_aware(value, "timestamp")

    @model_validator(mode="after")
    def _check_action_fields(self) -> PersonalCalendarInput:
        if self.action == "create":
            if not self.title:
                raise ValueError("action 'create' requires title")
            if self.start_utc is None or self.end_utc is None:
                raise ValueError("action 'create' requires start_utc and end_utc")
            if self.end_utc <= self.start_utc:
                raise ValueError("end_utc must be after start_utc")
        elif self.action == "update":
            if not self.event_id:
                raise ValueError("action 'update' requires event_id")
            if (
                self.title is None
                and self.description is None
                and self.start_utc is None
                and self.end_utc is None
            ):
                raise ValueError(
                    "action 'update' requires at least one of title, description, "
                    "start_utc, or end_utc"
                )
            if (self.start_utc is None) != (self.end_utc is None):
                raise ValueError("action 'update' requires start_utc and end_utc together")
        elif self.action == "delete":
            if not self.event_id:
                raise ValueError("action 'delete' requires event_id")
        if (
            self.action == "retrieve"
            and self.window_start_utc is not None
            and self.window_end_utc is not None
            and self.window_end_utc <= self.window_start_utc
        ):
            raise ValueError("window_end_utc must be after window_start_utc")
        return self


class PersonalReminderInput(BaseModel):
    """Validated input for the deterministic `personal.reminder` tool."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["create", "retrieve", "update", "cancel", "delete"] = Field(
        description="Operation to perform; 'retrieve' is SAFE, mutations require confirmation"
    )
    reminder_id: str | None = Field(
        default=None,
        pattern=_ID_PATTERN,
        description="Stable reminder identifier (required for update/cancel/delete)",
    )
    message: str | None = Field(
        default=None,
        min_length=1,
        max_length=REMINDER_MESSAGE_MAX,
        description="Reminder text (required for create)",
    )
    fire_utc: datetime | None = Field(
        default=None,
        description="Explicit timezone-aware UTC time the reminder should fire",
    )
    recurrence: ReminderRecurrence | None = Field(
        default=None,
        description="one_shot, daily, weekly, or monthly (default one_shot)",
    )
    max_fires: int | None = Field(
        default=None,
        ge=1,
        le=REMINDER_MAX_FIRES_LIMIT,
        description="Bounded maximum number of firings for recurring reminders",
    )
    limit: int = Field(
        default=LIST_LIMIT_DEFAULT,
        ge=1,
        le=LIST_LIMIT_MAX,
        description="Maximum number of reminders returned by a retrieve",
    )

    @field_validator("fire_utc")
    @classmethod
    def _utc_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_utc_aware(value, "fire_utc")

    @model_validator(mode="after")
    def _check_action_fields(self) -> PersonalReminderInput:
        if self.action == "create":
            if not self.message:
                raise ValueError("action 'create' requires message")
            if self.fire_utc is None:
                raise ValueError("action 'create' requires fire_utc")
            if (
                self.recurrence == ReminderRecurrence.ONE_SHOT
                and self.max_fires is not None
                and self.max_fires != 1
            ):
                raise ValueError("one_shot reminders are limited to max_fires=1")
        elif self.action == "update":
            if not self.reminder_id:
                raise ValueError("action 'update' requires reminder_id")
            if (
                self.message is None
                and self.fire_utc is None
                and self.recurrence is None
                and self.max_fires is None
            ):
                raise ValueError(
                    "action 'update' requires at least one of message, fire_utc, "
                    "recurrence, or max_fires"
                )
            if (
                self.recurrence == ReminderRecurrence.ONE_SHOT
                and self.max_fires is not None
                and self.max_fires != 1
            ):
                raise ValueError("one_shot reminders are limited to max_fires=1")
        elif self.action in ("cancel", "delete"):
            if not self.reminder_id:
                raise ValueError(f"action '{self.action}' requires reminder_id")
        return self


# ---------------------------------------------------------------------------
# Tool output schemas
# ---------------------------------------------------------------------------


class PersonalNoteOutput(BaseModel):
    """Structured outcome of a personal.note invocation."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["create", "retrieve", "update", "delete"]
    note: PersonalNoteRecord | None = None
    notes: list[PersonalNoteRecord] = Field(default_factory=list)
    count: int = Field(default=0, ge=0)
    deleted: bool = False


class PersonalCalendarOutput(BaseModel):
    """Structured outcome of a personal.calendar invocation."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["create", "retrieve", "update", "delete"]
    event: CalendarEventRecord | None = None
    events: list[CalendarEventRecord] = Field(default_factory=list)
    count: int = Field(default=0, ge=0)
    deleted: bool = False


class PersonalReminderOutput(BaseModel):
    """Structured outcome of a personal.reminder invocation."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["create", "retrieve", "update", "cancel", "delete"]
    reminder: ReminderRecord | None = None
    reminders: list[ReminderRecord] = Field(default_factory=list)
    count: int = Field(default=0, ge=0)
    deleted: bool = False


class ReminderDispatch(BaseModel):
    """Audit record for a reminder occurrence dispatched via ProactiveScheduler."""

    model_config = ConfigDict(extra="forbid")

    reminder_id: str = Field(min_length=5, max_length=64, pattern=_ID_PATTERN)
    message: str = Field(min_length=1, max_length=REMINDER_MESSAGE_MAX)
    occurrence_utc: datetime
    task_id: str | None = Field(
        default=None,
        description="ProactiveScheduler task identifier created for this occurrence",
    )

    @field_validator("occurrence_utc")
    @classmethod
    def _utc_aware(cls, value: datetime) -> datetime:
        return _require_utc_aware(value, "occurrence_utc")


__all__ = [
    "EVENT_DESCRIPTION_MAX",
    "EVENT_TITLE_MAX",
    "LIST_LIMIT_DEFAULT",
    "LIST_LIMIT_MAX",
    "NOTE_CONTENT_MAX",
    "NOTE_TAGS_MAX",
    "NOTE_TAG_LENGTH_MAX",
    "NOTE_TITLE_MAX",
    "REMINDER_DEFAULT_RECURRING_MAX_FIRES",
    "REMINDER_MAX_FIRES_LIMIT",
    "REMINDER_MESSAGE_MAX",
    "CalendarEventRecord",
    "PersonalCalendarInput",
    "PersonalCalendarOutput",
    "PersonalNoteInput",
    "PersonalNoteOutput",
    "PersonalNoteRecord",
    "PersonalReminderInput",
    "PersonalReminderOutput",
    "ReminderDispatch",
    "ReminderRecord",
    "ReminderRecurrence",
    "ReminderStatus",
]
