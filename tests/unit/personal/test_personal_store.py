"""Unit tests for the PersonalStore SQLite persistence layer (Phase 10 Iteration 4).

Adheres to:
- skills/python/memory-system (SQLite authority, migration safety)
- skills/coding/validation (strict bounds, UTC timestamps, stable ids)
- Security boundary: personal tables are separate from `memories` and never
  enter MemoryManager/Qdrant records.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from tom.memory.sqlite import SQLiteMemory
from tom.personal.store import (
    PERSONAL_SCHEMA_VERSION,
    PersonalNotFoundError,
    PersonalStore,
    PersonalStoreError,
    advance_occurrence,
    default_max_fires,
)
from tom.schemas.memory import MemoryQuery
from tom.schemas.personal import (
    NOTE_CONTENT_MAX,
    NOTE_TITLE_MAX,
    REMINDER_MESSAGE_MAX,
    ReminderRecurrence,
    ReminderStatus,
)


@pytest.fixture()
def store(tmp_path: Path):
    s = PersonalStore(db_path=str(tmp_path / "personal_test.db"))
    yield s
    s.close()


def _utc(year: int, month: int, day: int, hour: int = 9, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Notes: CRUD, stable ids, UTC timestamps, bounds
# ---------------------------------------------------------------------------


class TestNoteCRUD:
    def test_create_and_get_roundtrip(self, store: PersonalStore) -> None:
        created = store.create_note("Title", "Body text", ["tag-a"])
        assert created.note_id.startswith("note_")
        assert created.created_at.tzinfo is not None
        assert created.updated_at.tzinfo is not None

        fetched = store.get_note(created.note_id)
        assert fetched is not None
        assert fetched.note_id == created.note_id
        assert fetched.title == "Title"
        assert fetched.content == "Body text"
        assert fetched.tags == ["tag-a"]
        assert fetched.created_at == created.created_at

    def test_note_ids_are_stable_and_unique(self, store: PersonalStore) -> None:
        a = store.create_note("A", "1")
        b = store.create_note("B", "2")
        assert a.note_id != b.note_id
        assert store.get_note(a.note_id).note_id == a.note_id  # type: ignore[union-attr]

    def test_update_preserves_identity_and_created_at(self, store: PersonalStore) -> None:
        created = store.create_note("Old", "Old body")
        updated = store.update_note(created.note_id, {"title": "New", "content": "New body"})
        assert updated.note_id == created.note_id
        assert updated.created_at == created.created_at
        assert updated.title == "New"
        assert updated.updated_at >= created.updated_at

    def test_update_missing_note_raises(self, store: PersonalStore) -> None:
        with pytest.raises(PersonalNotFoundError):
            store.update_note("note_deadbeef0000", {"title": "x"})

    def test_delete_removes_and_reports(self, store: PersonalStore) -> None:
        created = store.create_note("Doomed", "x")
        assert store.delete_note(created.note_id) is True
        assert store.get_note(created.note_id) is None
        assert store.delete_note(created.note_id) is False

    def test_list_notes_query_and_limit(self, store: PersonalStore) -> None:
        store.create_note("Meeting notes", "discuss roadmap")
        store.create_note("Groceries", "milk and eggs")
        store.create_note("Roadmap detail", "q4 planning")

        assert len(store.list_notes()) == 3
        hits = store.list_notes(query="roadmap")
        assert len(hits) == 2
        assert len(store.list_notes(limit=1)) == 1

    def test_overlong_title_rejected_at_store_boundary(self, store: PersonalStore) -> None:
        with pytest.raises(ValidationError):
            store.create_note("x" * (NOTE_TITLE_MAX + 1), "ok")

    def test_overlong_content_rejected_at_store_boundary(self, store: PersonalStore) -> None:
        with pytest.raises(ValidationError):
            store.create_note("ok", "x" * (NOTE_CONTENT_MAX + 1))

    def test_empty_title_rejected(self, store: PersonalStore) -> None:
        with pytest.raises(ValidationError):
            store.create_note("", "ok")

    def test_invalid_update_fields_rejected(self, store: PersonalStore) -> None:
        created = store.create_note("A", "b")
        with pytest.raises(PersonalStoreError):
            store.update_note(created.note_id, {"note_id": "note_hacked00000"})


# ---------------------------------------------------------------------------
# Calendar: explicit UTC timestamps, bounds, deletion
# ---------------------------------------------------------------------------


class TestCalendarCRUD:
    def test_create_normalizes_to_utc(self, store: PersonalStore) -> None:
        start = datetime.fromisoformat("2026-10-09T17:00:00+05:30")
        end = datetime.fromisoformat("2026-10-09T18:00:00+05:30")
        event = store.create_event("Standup", start, end, "daily sync")
        assert event.start_utc.tzinfo is not None
        assert event.start_utc.utcoffset() == timedelta(0)
        assert event.start_utc.hour == 11  # 17:00+05:30 == 11:30 UTC
        assert event.event_id.startswith("evt_")

    def test_naive_datetime_rejected(self, store: PersonalStore) -> None:
        with pytest.raises(ValidationError):
            store.create_event("Bad", datetime(2026, 10, 9, 9, 0), datetime(2026, 10, 9, 10, 0))

    def test_end_before_start_rejected(self, store: PersonalStore) -> None:
        with pytest.raises(ValidationError):
            store.create_event("Bad", _utc(2026, 10, 9, 10), _utc(2026, 10, 9, 9))

    def test_update_and_delete(self, store: PersonalStore) -> None:
        event = store.create_event("Sync", _utc(2026, 10, 9, 9), _utc(2026, 10, 9, 10))
        updated = store.update_event(event.event_id, {"title": "Retitled"})
        assert updated.title == "Retitled"
        assert updated.event_id == event.event_id
        assert store.delete_event(event.event_id) is True
        assert store.get_event(event.event_id) is None
        assert store.delete_event(event.event_id) is False

    def test_update_requires_start_and_end_together(self, store: PersonalStore) -> None:
        event = store.create_event("Sync", _utc(2026, 10, 9, 9), _utc(2026, 10, 9, 10))
        with pytest.raises(PersonalStoreError):
            store.update_event(event.event_id, {"start_utc": _utc(2026, 10, 10, 9)})

    def test_window_filter(self, store: PersonalStore) -> None:
        store.create_event("Early", _utc(2026, 10, 1, 9), _utc(2026, 10, 1, 10))
        store.create_event("Late", _utc(2026, 12, 1, 9), _utc(2026, 12, 1, 10))
        in_october = store.list_events(
            window_start=_utc(2026, 10, 1), window_end=_utc(2026, 10, 31, 23, 59)
        )
        assert [e.title for e in in_october] == ["Early"]

    def test_missing_event_raises(self, store: PersonalStore) -> None:
        with pytest.raises(PersonalNotFoundError):
            store.update_event("evt_deadbeef0000", {"title": "x"})


# ---------------------------------------------------------------------------
# Reminders: recurrence, bounded firing, duplicate suppression
# ---------------------------------------------------------------------------


class TestReminderStore:
    def test_create_defaults(self, store: PersonalStore) -> None:
        r = store.create_reminder("Drink water", _utc(2026, 10, 10, 9))
        assert r.reminder_id.startswith("rem_")
        assert r.recurrence == ReminderRecurrence.ONE_SHOT
        assert r.max_fires == 1
        assert r.fires == 0
        assert r.status == ReminderStatus.ACTIVE
        assert r.last_fired_at is None

    def test_recurring_default_budget_is_bounded(self, store: PersonalStore) -> None:
        daily = store.create_reminder("d", _utc(2026, 10, 10, 9), ReminderRecurrence.DAILY)
        assert daily.max_fires == 100

    def test_overlong_message_rejected(self, store: PersonalStore) -> None:
        with pytest.raises(ValidationError):
            store.create_reminder("x" * (REMINDER_MESSAGE_MAX + 1), _utc(2026, 10, 10, 9))

    def test_due_reminders_filter(self, store: PersonalStore) -> None:
        store.create_reminder("due", _utc(2026, 10, 10, 9))
        store.create_reminder("later", _utc(2026, 10, 12, 9))
        due = store.due_reminders(_utc(2026, 10, 11, 0))
        assert [r.message for r in due] == ["due"]

    def test_fire_advances_daily_without_drift(self, store: PersonalStore) -> None:
        r = store.create_reminder("d", _utc(2026, 10, 10, 9), ReminderRecurrence.DAILY)
        fired = store.record_reminder_fire(r.reminder_id, 0, _utc(2026, 10, 10, 9))
        assert fired is not None
        assert fired.fires == 1
        assert fired.status == ReminderStatus.ACTIVE
        assert fired.fire_utc == _utc(2026, 10, 11, 9)
        assert fired.last_fired_at == _utc(2026, 10, 10, 9)

    def test_fire_duplicate_suppressed_by_stale_counter(self, store: PersonalStore) -> None:
        r = store.create_reminder("d", _utc(2026, 10, 10, 9), ReminderRecurrence.DAILY)
        assert store.record_reminder_fire(r.reminder_id, 0, _utc(2026, 10, 10, 9))
        # Second fire with the stale expected counter must be a no-op.
        assert store.record_reminder_fire(r.reminder_id, 0, _utc(2026, 10, 10, 10)) is None
        assert store.get_reminder(r.reminder_id).fires == 1  # type: ignore[union-attr]

    def test_one_shot_completes_after_single_fire(self, store: PersonalStore) -> None:
        r = store.create_reminder("once", _utc(2026, 10, 10, 9))
        fired = store.record_reminder_fire(r.reminder_id, 0, _utc(2026, 10, 10, 9))
        assert fired is not None
        assert fired.status == ReminderStatus.COMPLETED
        assert store.due_reminders(_utc(2026, 10, 20, 9)) == []

    def test_bounded_recurrence_completes_at_max_fires(self, store: PersonalStore) -> None:
        r = store.create_reminder("d", _utc(2026, 10, 10, 9), ReminderRecurrence.DAILY, max_fires=2)
        store.record_reminder_fire(r.reminder_id, 0, _utc(2026, 10, 10, 9))
        store.record_reminder_fire(r.reminder_id, 1, _utc(2026, 10, 11, 9))
        final = store.get_reminder(r.reminder_id)
        assert final is not None
        assert final.fires == 2
        assert final.status == ReminderStatus.COMPLETED
        assert store.due_reminders(_utc(2026, 12, 1, 9)) == []

    def test_monthly_recurrence_clamps_day(self, store: PersonalStore) -> None:
        r = store.create_reminder("m", _utc(2026, 1, 31, 9), ReminderRecurrence.MONTHLY)
        fired = store.record_reminder_fire(r.reminder_id, 0, _utc(2026, 1, 31, 9))
        assert fired is not None
        assert fired.fire_utc == _utc(2026, 2, 28, 9)

    def test_cancel_idempotent_and_completed_cannot_cancel(self, store: PersonalStore) -> None:
        r = store.create_reminder("c", _utc(2026, 10, 10, 9))
        cancelled = store.cancel_reminder(r.reminder_id)
        assert cancelled.status == ReminderStatus.CANCELLED
        assert store.cancel_reminder(r.reminder_id).status == ReminderStatus.CANCELLED

        done = store.create_reminder("d", _utc(2026, 10, 10, 9))
        store.record_reminder_fire(done.reminder_id, 0, _utc(2026, 10, 10, 9))
        with pytest.raises(PersonalStoreError):
            store.cancel_reminder(done.reminder_id)

    def test_update_rejects_budget_below_fires(self, store: PersonalStore) -> None:
        r = store.create_reminder("d", _utc(2026, 10, 10, 9), ReminderRecurrence.DAILY, max_fires=5)
        store.record_reminder_fire(r.reminder_id, 0, _utc(2026, 10, 10, 9))
        with pytest.raises(PersonalStoreError):
            store.update_reminder(r.reminder_id, {"max_fires": 1})

    def test_update_to_one_shot_pins_budget(self, store: PersonalStore) -> None:
        r = store.create_reminder(
            "d", _utc(2026, 10, 10, 9), ReminderRecurrence.DAILY, max_fires=50
        )
        updated = store.update_reminder(r.reminder_id, {"recurrence": "one_shot"})
        assert updated.recurrence == ReminderRecurrence.ONE_SHOT
        assert updated.max_fires == 1

    def test_update_of_cancelled_reminder_rejected(self, store: PersonalStore) -> None:
        r = store.create_reminder("c", _utc(2026, 10, 10, 9))
        store.cancel_reminder(r.reminder_id)
        with pytest.raises(PersonalStoreError):
            store.update_reminder(r.reminder_id, {"message": "new"})

    def test_delete_reminder(self, store: PersonalStore) -> None:
        r = store.create_reminder("x", _utc(2026, 10, 10, 9))
        assert store.delete_reminder(r.reminder_id) is True
        assert store.get_reminder(r.reminder_id) is None
        assert store.delete_reminder(r.reminder_id) is False


# ---------------------------------------------------------------------------
# Occurrence math
# ---------------------------------------------------------------------------


class TestAdvanceOccurrence:
    def test_one_shot_returns_none(self) -> None:
        assert advance_occurrence(_utc(2026, 10, 10), ReminderRecurrence.ONE_SHOT) is None

    def test_daily_and_weekly(self) -> None:
        assert advance_occurrence(_utc(2026, 10, 10), ReminderRecurrence.DAILY) == _utc(
            2026, 10, 11
        )
        assert advance_occurrence(_utc(2026, 10, 10), ReminderRecurrence.WEEKLY) == _utc(
            2026, 10, 17
        )

    def test_monthly_year_rollover_and_clamp(self) -> None:
        assert advance_occurrence(_utc(2026, 12, 15), ReminderRecurrence.MONTHLY) == _utc(
            2027, 1, 15
        )
        assert advance_occurrence(_utc(2026, 1, 31), ReminderRecurrence.MONTHLY) == _utc(
            2026, 2, 28
        )

    def test_default_max_fires(self) -> None:
        assert default_max_fires(ReminderRecurrence.ONE_SHOT) == 1
        assert default_max_fires(ReminderRecurrence.DAILY) == 100


# ---------------------------------------------------------------------------
# Migration & persistence safety (single shared DB, non-destructive)
# ---------------------------------------------------------------------------


class TestMigrationAndPersistence:
    def test_schema_version_applied(self, tmp_path: Path) -> None:
        db = tmp_path / "migrate.db"
        s = PersonalStore(db_path=str(db))
        assert s.schema_version() == PERSONAL_SCHEMA_VERSION
        s.close()

    def test_reopen_is_idempotent_and_preserves_data(self, tmp_path: Path) -> None:
        db = tmp_path / "persist.db"
        s1 = PersonalStore(db_path=str(db))
        note = s1.create_note("Kept", "across reopen")
        s1.close()

        s2 = PersonalStore(db_path=str(db))  # migration must not destroy rows
        try:
            assert s2.schema_version() == PERSONAL_SCHEMA_VERSION
            assert s2.get_note(note.note_id) is not None
        finally:
            s2.close()

    def test_shares_single_database_with_sqlite_memory(self, tmp_path: Path) -> None:
        """Personal data uses the EXISTING database file — never a second one."""
        db = tmp_path / "shared.db"
        memory = SQLiteMemory(db_path=str(db))
        personal = PersonalStore(db_path=str(db))
        try:
            personal.create_note("Private", "personal detail")
            tables = {
                row["name"]
                for row in memory._get_connection()
                .execute("SELECT name FROM sqlite_master WHERE type='table';")
                .fetchall()
            }
            assert "memories" in tables
            assert "personal_notes" in tables
            assert "personal_calendar_events" in tables
            assert "personal_reminders" in tables

            # Personal records must not appear in semantic memory search results.
            results = memory.search(MemoryQuery(text="Private personal detail"))
            assert results == []
        finally:
            personal.close()
            memory.close()

    def test_migration_statements_are_create_only(self) -> None:
        """No destructive DDL is permitted in the personal migration path."""
        from tom.personal.store import _SCHEMA_V1

        for statement in _SCHEMA_V1:
            upper = statement.upper()
            assert "DROP" not in upper
            assert "DELETE" not in upper
            assert "ALTER" not in upper
