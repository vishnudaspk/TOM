"""TOM personal productivity subsystem (Phase 10 Iteration 4).

Provides SQLite-backed persistence for notes, calendar events, and reminders
plus the scheduler-safe ReminderService bridge to the existing ProactiveScheduler.
This is a persistence/tool-support module — NOT a memory subsystem: records here
never enter MemoryManager, Qdrant, or automatic model context.
"""

from tom.personal.reminders import ReminderService, build_reminder_trigger
from tom.personal.store import (
    PERSONAL_SCHEMA_VERSION,
    PersonalNotFoundError,
    PersonalStore,
    PersonalStoreError,
    advance_occurrence,
    default_max_fires,
)

__all__ = [
    "PERSONAL_SCHEMA_VERSION",
    "PersonalNotFoundError",
    "PersonalStore",
    "PersonalStoreError",
    "ReminderService",
    "advance_occurrence",
    "build_reminder_trigger",
    "default_max_fires",
]
