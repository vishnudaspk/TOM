"""TOM Personal Productivity Tools (Phase 10 Iteration 4).

Provides typed, deterministic personal tools integrating with the existing
canonical pipeline (ToolRegistry -> ToolExecutor -> PermissionEngine):

- personal.note        (create / retrieve / update / delete)
- personal.calendar    (create / retrieve / update / delete)
- personal.reminder    (create / retrieve / update / cancel / delete)

Adheres to:
- Decision 032: Centralized Tool Invocation Safety (strict ToolExecutor routing).
- SQLite is authoritative for personal data (shared existing database file with
  dedicated tables); Qdrant and MemoryManager are never involved.
- Reads are SAFE; mutations require ASK_USER — enforced centrally by
  PermissionEngine via PERSONAL_ACTION_ESCALATION (fail-safe).
- Reminders delegate lifecycle scheduling to ReminderService / ProactiveScheduler;
  no arbitrary task-execution mechanism exists here.
"""

from __future__ import annotations

import asyncio

from tom.personal.reminders import ReminderService
from tom.personal.store import PersonalNotFoundError, PersonalStore, PersonalStoreError
from tom.schemas.personal import (
    PersonalCalendarInput,
    PersonalCalendarOutput,
    PersonalNoteInput,
    PersonalNoteOutput,
    PersonalReminderInput,
    PersonalReminderOutput,
    ReminderRecurrence,
)
from tom.security.permissions import PermissionLevel
from tom.tools.registry import ToolDefinition, ToolRegistry, default_registry

_NOTE_TIMEOUT_SECONDS = 10.0
_CALENDAR_TIMEOUT_SECONDS = 10.0
_REMINDER_TIMEOUT_SECONDS = 15.0


def create_personal_tools(
    registry: ToolRegistry | None = None,
    store: PersonalStore | None = None,
    reminder_service: ReminderService | None = None,
    replace: bool = True,
) -> list[ToolDefinition]:
    """Create and register the three personal productivity tools.

    Args:
        registry: Target ToolRegistry (defaults to default_registry).
        store: Injected PersonalStore; when None a default store is lazily
            constructed from MemoryConfig.sqlite_db_path on first use so that
            registration itself performs no database work.
        reminder_service: Injected ReminderService bridging ProactiveScheduler.
            When None it is lazily constructed over the resolved store.
        replace: Whether to overwrite existing registrations (default True).

    Returns:
        List of registered ToolDefinition instances (personal.note,
        personal.calendar, personal.reminder).
    """
    target_registry = registry if registry is not None else default_registry
    _store = store
    _service = reminder_service

    def _get_store() -> PersonalStore:
        nonlocal _store
        if _service is not None:
            return _service.store
        if _store is None:
            _store = PersonalStore()
        return _store

    def _get_service() -> ReminderService:
        nonlocal _service
        if _service is None:
            _service = ReminderService(_get_store())
        return _service

    async def note(params: PersonalNoteInput) -> PersonalNoteOutput:
        """Deterministic note CRUD backed by the personal SQLite store."""
        personal_store = _get_store()
        if params.action == "create":
            if not params.title or not params.content:
                raise PersonalStoreError("action 'create' requires title and content")
            record = await asyncio.to_thread(
                personal_store.create_note,
                params.title,
                params.content,
                params.tags,
            )
            return PersonalNoteOutput(operation="create", note=record, count=1)

        if params.action == "retrieve":
            if params.note_id:
                record = await asyncio.to_thread(personal_store.get_note, params.note_id)
                if record is None:
                    raise PersonalNotFoundError(f"note '{params.note_id}' not found")
                return PersonalNoteOutput(operation="retrieve", note=record, count=1)
            notes = await asyncio.to_thread(personal_store.list_notes, params.query, params.limit)
            return PersonalNoteOutput(operation="retrieve", notes=notes, count=len(notes))

        if not params.note_id:
            raise PersonalStoreError(f"action '{params.action}' requires note_id")

        if params.action == "update":
            updates = {
                key: value
                for key, value in (
                    ("title", params.title),
                    ("content", params.content),
                    ("tags", params.tags),
                )
                if value is not None
            }
            record = await asyncio.to_thread(personal_store.update_note, params.note_id, updates)
            return PersonalNoteOutput(operation="update", note=record, count=1)

        deleted = await asyncio.to_thread(personal_store.delete_note, params.note_id)
        if not deleted:
            raise PersonalNotFoundError(f"note '{params.note_id}' not found")
        return PersonalNoteOutput(operation="delete", deleted=True, count=1)

    async def calendar(params: PersonalCalendarInput) -> PersonalCalendarOutput:
        """Deterministic calendar CRUD with explicit UTC timestamps."""
        personal_store = _get_store()
        if params.action == "create":
            if not params.title or params.start_utc is None or params.end_utc is None:
                raise PersonalStoreError("action 'create' requires title, start_utc, and end_utc")
            record = await asyncio.to_thread(
                personal_store.create_event,
                params.title,
                params.start_utc,
                params.end_utc,
                params.description or "",
            )
            return PersonalCalendarOutput(operation="create", event=record, count=1)

        if params.action == "retrieve":
            if params.event_id:
                record = await asyncio.to_thread(personal_store.get_event, params.event_id)
                if record is None:
                    raise PersonalNotFoundError(f"event '{params.event_id}' not found")
                return PersonalCalendarOutput(operation="retrieve", event=record, count=1)
            events = await asyncio.to_thread(
                personal_store.list_events,
                params.window_start_utc,
                params.window_end_utc,
                params.limit,
            )
            return PersonalCalendarOutput(operation="retrieve", events=events, count=len(events))

        if not params.event_id:
            raise PersonalStoreError(f"action '{params.action}' requires event_id")

        if params.action == "update":
            updates = {
                key: value
                for key, value in (
                    ("title", params.title),
                    ("description", params.description),
                    ("start_utc", params.start_utc),
                    ("end_utc", params.end_utc),
                )
                if value is not None
            }
            record = await asyncio.to_thread(personal_store.update_event, params.event_id, updates)
            return PersonalCalendarOutput(operation="update", event=record, count=1)

        deleted = await asyncio.to_thread(personal_store.delete_event, params.event_id)
        if not deleted:
            raise PersonalNotFoundError(f"event '{params.event_id}' not found")
        return PersonalCalendarOutput(operation="delete", deleted=True, count=1)

    async def reminder(params: PersonalReminderInput) -> PersonalReminderOutput:
        """Reminder CRUD with ProactiveScheduler-safe lifecycle semantics."""
        personal_store = _get_store()
        service = _get_service()

        if params.action == "create":
            if not params.message or params.fire_utc is None:
                raise PersonalStoreError("action 'create' requires message and fire_utc")
            record = await asyncio.to_thread(
                personal_store.create_reminder,
                params.message,
                params.fire_utc,
                params.recurrence or ReminderRecurrence.ONE_SHOT,
                params.max_fires,
            )
            await service.sync_from_store(record.reminder_id)
            return PersonalReminderOutput(operation="create", reminder=record, count=1)

        if params.action == "retrieve":
            if params.reminder_id:
                record = await asyncio.to_thread(personal_store.get_reminder, params.reminder_id)
                if record is None:
                    raise PersonalNotFoundError(f"reminder '{params.reminder_id}' not found")
                return PersonalReminderOutput(operation="retrieve", reminder=record, count=1)
            reminders = await asyncio.to_thread(personal_store.list_reminders, None, params.limit)
            return PersonalReminderOutput(
                operation="retrieve", reminders=reminders, count=len(reminders)
            )

        if not params.reminder_id:
            raise PersonalStoreError(f"action '{params.action}' requires reminder_id")

        if params.action == "update":
            updates = {
                key: value
                for key, value in (
                    ("message", params.message),
                    ("fire_utc", params.fire_utc),
                    ("recurrence", params.recurrence),
                    ("max_fires", params.max_fires),
                )
                if value is not None
            }
            record = await asyncio.to_thread(
                personal_store.update_reminder, params.reminder_id, updates
            )
            await service.sync_from_store(record.reminder_id)
            return PersonalReminderOutput(operation="update", reminder=record, count=1)

        if params.action == "cancel":
            record = await service.cancel(params.reminder_id)
            return PersonalReminderOutput(operation="cancel", reminder=record, count=1)

        deleted = await service.delete(params.reminder_id)
        if not deleted:
            raise PersonalNotFoundError(f"reminder '{params.reminder_id}' not found")
        return PersonalReminderOutput(operation="delete", deleted=True, count=1)

    definitions = [
        ToolDefinition(
            name="personal.note",
            description=(
                "Create, retrieve, update, or delete personal notes stored locally in "
                "SQLite. Retrieval is SAFE; create/update/delete require user "
                "confirmation. Personal notes never enter semantic memory."
            ),
            category="personal",
            input_schema=PersonalNoteInput,
            output_schema=PersonalNoteOutput,
            permission_level=PermissionLevel.SAFE,
            timeout_seconds=_NOTE_TIMEOUT_SECONDS,
            handler=note,
        ),
        ToolDefinition(
            name="personal.calendar",
            description=(
                "Create, retrieve, update, or delete personal calendar events with "
                "explicit UTC timestamps stored locally in SQLite. Retrieval is SAFE; "
                "create/update/delete require user confirmation. No external calendar "
                "synchronization."
            ),
            category="personal",
            input_schema=PersonalCalendarInput,
            output_schema=PersonalCalendarOutput,
            permission_level=PermissionLevel.SAFE,
            timeout_seconds=_CALENDAR_TIMEOUT_SECONDS,
            handler=calendar,
        ),
        ToolDefinition(
            name="personal.reminder",
            description=(
                "Create, retrieve, update, cancel, or delete personal reminders with "
                "one_shot/daily/weekly/monthly recurrence. Retrieval is SAFE; all "
                "mutations require user confirmation. Due reminders fire through the "
                "existing proactive scheduler policy (quiet hours, preemption, rate "
                "limits)."
            ),
            category="personal",
            input_schema=PersonalReminderInput,
            output_schema=PersonalReminderOutput,
            permission_level=PermissionLevel.SAFE,
            timeout_seconds=_REMINDER_TIMEOUT_SECONDS,
            handler=reminder,
        ),
    ]

    for definition in definitions:
        target_registry.register(definition, replace=replace)
    return definitions


__all__ = ["create_personal_tools"]
