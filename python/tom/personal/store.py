"""PersonalStore — authoritative SQLite persistence for personal productivity data.

Adheres to:
- Phase 10 Architecture (personal.note, personal.calendar, personal.reminder)
- Decision 037 boundary: SQLite is the source of truth — this store extends the
  *existing* SQLite database file (MemoryConfig.sqlite_db_path) with dedicated
  personal tables. No second database and no parallel persistence framework.
- skills/python/memory-system: agents never touch SQL directly; the typed tool
  pipeline is the only entry point.
- skills/coding/validation: strict bounds, timezone-aware UTC datetimes,
  stable identifiers, migration-safe forward-only schema changes.

Security boundaries:
- Personal rows live in dedicated tables (`personal_notes`,
  `personal_calendar_events`, `personal_reminders`) and are never written into
  the `memories` table, MemoryManager records, or Qdrant.
- Migrations are forward-only and non-destructive (CREATE ... IF NOT EXISTS);
  existing tables are never dropped or altered destructively.
"""

from __future__ import annotations

import calendar
import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tom.schemas.config import MemoryConfig
from tom.schemas.personal import (
    REMINDER_DEFAULT_RECURRING_MAX_FIRES,
    CalendarEventRecord,
    PersonalNoteRecord,
    ReminderRecord,
    ReminderRecurrence,
    ReminderStatus,
)
from tom.telemetry.logging import get_logger

logger = get_logger(__name__, component="personal.store")

PERSONAL_SCHEMA_VERSION = 1

# Forward-only, non-destructive migration statements (version 1).
# Never DROP or rewrite existing tables — additive schema changes only.
_SCHEMA_V1: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS personal_notes (
        note_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        tags TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_personal_notes_created ON personal_notes(created_at);",
    """
    CREATE TABLE IF NOT EXISTS personal_calendar_events (
        event_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        start_utc TEXT NOT NULL,
        end_utc TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_personal_events_start ON personal_calendar_events(start_utc);",
    """
    CREATE TABLE IF NOT EXISTS personal_reminders (
        reminder_id TEXT PRIMARY KEY,
        message TEXT NOT NULL,
        fire_utc TEXT NOT NULL,
        recurrence TEXT NOT NULL,
        max_fires INTEGER NOT NULL,
        fires INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'active',
        last_fired_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_personal_reminders_due ON personal_reminders(status, fire_utc);",
)


class PersonalStoreError(Exception):
    """Base exception for personal productivity persistence errors."""


class PersonalNotFoundError(PersonalStoreError):
    """Raised when a requested personal record does not exist."""


def _new_id(prefix: str) -> str:
    """Generate a stable, prefixed, collision-resistant record identifier."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def advance_occurrence(fire_utc: datetime, recurrence: ReminderRecurrence) -> datetime | None:
    """Return the next occurrence after ``fire_utc`` for a bounded recurrence.

    Returns ``None`` when no further occurrence exists (one-shot).
    Monthly recurrence clamps the day to the target month length (Jan 31 -> Feb 28).
    All arithmetic is performed in UTC.
    """
    if recurrence == ReminderRecurrence.ONE_SHOT:
        return None
    if recurrence == ReminderRecurrence.DAILY:
        return fire_utc + timedelta(days=1)
    if recurrence == ReminderRecurrence.WEEKLY:
        return fire_utc + timedelta(days=7)
    # MONTHLY
    month = fire_utc.month + 1
    year = fire_utc.year
    if month > 12:
        month -= 12
        year += 1
    day = min(fire_utc.day, calendar.monthrange(year, month)[1])
    return fire_utc.replace(year=year, month=month, day=day)


def default_max_fires(recurrence: ReminderRecurrence) -> int:
    """Bounded default firing budget for a recurrence class."""
    if recurrence == ReminderRecurrence.ONE_SHOT:
        return 1
    return REMINDER_DEFAULT_RECURRING_MAX_FIRES


def _dt_to_str(value: datetime) -> str:
    """Serialize a timezone-aware datetime as an explicit UTC ISO string."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise PersonalStoreError("personal records require timezone-aware UTC timestamps")
    return value.astimezone(UTC).isoformat()


def _str_to_dt(value: str) -> datetime:
    """Parse a stored ISO timestamp into an aware UTC datetime."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class PersonalStore:
    """Relational store for personal notes, calendar events, and reminders.

    Shares the existing authoritative SQLite database file with ``SQLiteMemory``
    but keeps completely separate tables. Thread-safe (RLock + shared connection),
    UTC timestamps, stable identifiers, and forward-only migrations.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = str(db_path) if db_path is not None else MemoryConfig().sqlite_db_path
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # ------------------------------------------------------------------
    # Connection & migration
    # ------------------------------------------------------------------

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            if self.db_path != ":memory:":
                parent = Path(self.db_path).parent
                parent.mkdir(parents=True, exist_ok=True)

            self._conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=10.0,
            )
            self._conn.row_factory = sqlite3.Row

            if self.db_path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute("PRAGMA busy_timeout=5000;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
        return self._conn

    def _init_db(self) -> None:
        """Apply forward-only, non-destructive migrations for personal tables."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                version = int(conn.execute("PRAGMA user_version;").fetchone()[0])
                if version < PERSONAL_SCHEMA_VERSION:
                    for statement in _SCHEMA_V1:
                        conn.execute(statement)
                    conn.execute(f"PRAGMA user_version = {PERSONAL_SCHEMA_VERSION};")
                    logger.info(
                        "personal_schema_migrated",
                        from_version=version,
                        to_version=PERSONAL_SCHEMA_VERSION,
                        db_path=self.db_path,
                    )

    def schema_version(self) -> int:
        """Return the current SQLite user_version for the shared database file."""
        with self._lock:
            conn = self._get_connection()
            return int(conn.execute("PRAGMA user_version;").fetchone()[0])

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ------------------------------------------------------------------
    # Notes
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_note(row: sqlite3.Row) -> PersonalNoteRecord:
        return PersonalNoteRecord(
            note_id=row["note_id"],
            title=row["title"],
            content=row["content"],
            tags=json.loads(row["tags"]),
            created_at=_str_to_dt(row["created_at"]),
            updated_at=_str_to_dt(row["updated_at"]),
        )

    def create_note(
        self,
        title: str,
        content: str,
        tags: list[str] | None = None,
    ) -> PersonalNoteRecord:
        """Create a note with a stable id and UTC timestamps."""
        now = datetime.now(UTC)
        record = PersonalNoteRecord(
            note_id=_new_id("note"),
            title=title,
            content=content,
            tags=tags or [],
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    "INSERT INTO personal_notes "
                    "(note_id, title, content, tags, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?);",
                    (
                        record.note_id,
                        record.title,
                        record.content,
                        json.dumps(record.tags),
                        _dt_to_str(record.created_at),
                        _dt_to_str(record.updated_at),
                    ),
                )
        logger.info("personal_note_created", note_id=record.note_id)
        return record

    def get_note(self, note_id: str) -> PersonalNoteRecord | None:
        """Fetch a single note by stable id."""
        with self._lock:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT * FROM personal_notes WHERE note_id = ?;", (note_id,)
            ).fetchone()
        return self._row_to_note(row) if row is not None else None

    def list_notes(self, query: str | None = None, limit: int = 20) -> list[PersonalNoteRecord]:
        """List recent notes with an optional case-insensitive substring filter."""
        with self._lock:
            conn = self._get_connection()
            rows = conn.execute("SELECT * FROM personal_notes ORDER BY created_at DESC;").fetchall()
        records = [self._row_to_note(row) for row in rows]
        if query:
            needle = query.lower()
            records = [
                r for r in records if needle in r.title.lower() or needle in r.content.lower()
            ]
        return records[:limit]

    def update_note(self, note_id: str, updates: dict[str, Any]) -> PersonalNoteRecord:
        """Update provided note fields; id and created_at are never modified."""
        allowed = {"title", "content", "tags"}
        if not updates or not set(updates) <= allowed:
            raise PersonalStoreError("invalid note update fields")
        with self._lock:
            existing = self.get_note(note_id)
            if existing is None:
                raise PersonalNotFoundError(f"note '{note_id}' not found")
            merged = existing.model_dump()
            merged.update(updates)
            merged["updated_at"] = datetime.now(UTC)
            record = PersonalNoteRecord.model_validate(merged)
            conn = self._get_connection()
            with conn:
                conn.execute(
                    "UPDATE personal_notes SET title = ?, content = ?, tags = ?, "
                    "updated_at = ? WHERE note_id = ?;",
                    (
                        record.title,
                        record.content,
                        json.dumps(record.tags),
                        _dt_to_str(record.updated_at),
                        note_id,
                    ),
                )
        return record

    def delete_note(self, note_id: str) -> bool:
        """Delete a note row; returns False when the id does not exist."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                cur = conn.execute("DELETE FROM personal_notes WHERE note_id = ?;", (note_id,))
            deleted = cur.rowcount > 0
        if deleted:
            logger.info("personal_note_deleted", note_id=note_id)
        return deleted

    # ------------------------------------------------------------------
    # Calendar events
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> CalendarEventRecord:
        return CalendarEventRecord(
            event_id=row["event_id"],
            title=row["title"],
            description=row["description"],
            start_utc=_str_to_dt(row["start_utc"]),
            end_utc=_str_to_dt(row["end_utc"]),
            created_at=_str_to_dt(row["created_at"]),
            updated_at=_str_to_dt(row["updated_at"]),
        )

    def create_event(
        self,
        title: str,
        start_utc: datetime,
        end_utc: datetime,
        description: str = "",
    ) -> CalendarEventRecord:
        """Create a calendar event with a stable id and explicit UTC timestamps."""
        now = datetime.now(UTC)
        record = CalendarEventRecord(
            event_id=_new_id("evt"),
            title=title,
            description=description or "",
            start_utc=start_utc,
            end_utc=end_utc,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    "INSERT INTO personal_calendar_events "
                    "(event_id, title, description, start_utc, end_utc, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?);",
                    (
                        record.event_id,
                        record.title,
                        record.description,
                        _dt_to_str(record.start_utc),
                        _dt_to_str(record.end_utc),
                        _dt_to_str(record.created_at),
                        _dt_to_str(record.updated_at),
                    ),
                )
        logger.info("personal_event_created", event_id=record.event_id)
        return record

    def get_event(self, event_id: str) -> CalendarEventRecord | None:
        """Fetch a single event by stable id."""
        with self._lock:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT * FROM personal_calendar_events WHERE event_id = ?;", (event_id,)
            ).fetchone()
        return self._row_to_event(row) if row is not None else None

    def list_events(
        self,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        limit: int = 20,
    ) -> list[CalendarEventRecord]:
        """List events ordered by start time, optionally within a UTC window."""
        with self._lock:
            conn = self._get_connection()
            rows = conn.execute(
                "SELECT * FROM personal_calendar_events ORDER BY start_utc ASC;"
            ).fetchall()
        records = [self._row_to_event(row) for row in rows]
        if window_start is not None:
            records = [r for r in records if r.end_utc >= window_start]
        if window_end is not None:
            records = [r for r in records if r.start_utc <= window_end]
        return records[:limit]

    def update_event(self, event_id: str, updates: dict[str, Any]) -> CalendarEventRecord:
        """Update provided event fields; id and created_at are never modified."""
        allowed = {"title", "description", "start_utc", "end_utc"}
        if not updates or not set(updates) <= allowed:
            raise PersonalStoreError("invalid calendar update fields")
        if ("start_utc" in updates) != ("end_utc" in updates):
            raise PersonalStoreError("calendar updates must provide start_utc and end_utc together")
        with self._lock:
            existing = self.get_event(event_id)
            if existing is None:
                raise PersonalNotFoundError(f"event '{event_id}' not found")
            merged = existing.model_dump()
            merged.update(updates)
            merged["updated_at"] = datetime.now(UTC)
            record = CalendarEventRecord.model_validate(merged)
            conn = self._get_connection()
            with conn:
                conn.execute(
                    "UPDATE personal_calendar_events SET title = ?, description = ?, "
                    "start_utc = ?, end_utc = ?, updated_at = ? WHERE event_id = ?;",
                    (
                        record.title,
                        record.description,
                        _dt_to_str(record.start_utc),
                        _dt_to_str(record.end_utc),
                        _dt_to_str(record.updated_at),
                        event_id,
                    ),
                )
        return record

    def delete_event(self, event_id: str) -> bool:
        """Delete an event row; returns False when the id does not exist."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                cur = conn.execute(
                    "DELETE FROM personal_calendar_events WHERE event_id = ?;", (event_id,)
                )
            deleted = cur.rowcount > 0
        if deleted:
            logger.info("personal_event_deleted", event_id=event_id)
        return deleted

    # ------------------------------------------------------------------
    # Reminders
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_reminder(row: sqlite3.Row) -> ReminderRecord:
        last_fired = row["last_fired_at"]
        return ReminderRecord(
            reminder_id=row["reminder_id"],
            message=row["message"],
            fire_utc=_str_to_dt(row["fire_utc"]),
            recurrence=ReminderRecurrence(row["recurrence"]),
            max_fires=int(row["max_fires"]),
            fires=int(row["fires"]),
            status=ReminderStatus(row["status"]),
            last_fired_at=_str_to_dt(last_fired) if last_fired else None,
            created_at=_str_to_dt(row["created_at"]),
            updated_at=_str_to_dt(row["updated_at"]),
        )

    def create_reminder(
        self,
        message: str,
        fire_utc: datetime,
        recurrence: ReminderRecurrence = ReminderRecurrence.ONE_SHOT,
        max_fires: int | None = None,
    ) -> ReminderRecord:
        """Create a reminder with bounded recurrence and a stable id."""
        effective_max = max_fires if max_fires is not None else default_max_fires(recurrence)
        if recurrence == ReminderRecurrence.ONE_SHOT:
            effective_max = 1
        now = datetime.now(UTC)
        record = ReminderRecord(
            reminder_id=_new_id("rem"),
            message=message,
            fire_utc=fire_utc,
            recurrence=recurrence,
            max_fires=effective_max,
            fires=0,
            status=ReminderStatus.ACTIVE,
            last_fired_at=None,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    "INSERT INTO personal_reminders "
                    "(reminder_id, message, fire_utc, recurrence, max_fires, fires, "
                    " status, last_fired_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
                    (
                        record.reminder_id,
                        record.message,
                        _dt_to_str(record.fire_utc),
                        record.recurrence.value,
                        record.max_fires,
                        record.fires,
                        record.status.value,
                        None,
                        _dt_to_str(record.created_at),
                        _dt_to_str(record.updated_at),
                    ),
                )
        logger.info(
            "personal_reminder_created",
            reminder_id=record.reminder_id,
            recurrence=record.recurrence.value,
        )
        return record

    def get_reminder(self, reminder_id: str) -> ReminderRecord | None:
        """Fetch a single reminder by stable id."""
        with self._lock:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT * FROM personal_reminders WHERE reminder_id = ?;", (reminder_id,)
            ).fetchone()
        return self._row_to_reminder(row) if row is not None else None

    def list_reminders(
        self, status: ReminderStatus | None = None, limit: int = 20
    ) -> list[ReminderRecord]:
        """List reminders ordered by next fire time, optionally filtered by status."""
        with self._lock:
            conn = self._get_connection()
            if status is None:
                rows = conn.execute(
                    "SELECT * FROM personal_reminders ORDER BY fire_utc ASC;"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM personal_reminders WHERE status = ? ORDER BY fire_utc ASC;",
                    (status.value,),
                ).fetchall()
        return [self._row_to_reminder(row) for row in rows[:limit]]

    def update_reminder(self, reminder_id: str, updates: dict[str, Any]) -> ReminderRecord:
        """Update provided reminder fields on an active reminder.

        The firing budget is re-validated: ``max_fires`` must remain strictly
        greater than the number of firings already consumed, and a switch to
        one-shot recurrence pins ``max_fires`` to 1.
        """
        allowed = {"message", "fire_utc", "recurrence", "max_fires"}
        if not updates or not set(updates) <= allowed:
            raise PersonalStoreError("invalid reminder update fields")
        with self._lock:
            existing = self.get_reminder(reminder_id)
            if existing is None:
                raise PersonalNotFoundError(f"reminder '{reminder_id}' not found")
            if existing.status != ReminderStatus.ACTIVE:
                raise PersonalStoreError(
                    f"reminder '{reminder_id}' is {existing.status.value}; "
                    f"only active reminders can be updated"
                )
            merged = existing.model_dump()
            merged.update(updates)
            recurrence = ReminderRecurrence(merged["recurrence"])
            if recurrence == ReminderRecurrence.ONE_SHOT:
                merged["max_fires"] = 1
            if int(merged["max_fires"]) <= existing.fires:
                raise PersonalStoreError(
                    "max_fires must be greater than the number of firings already used"
                )
            merged["updated_at"] = datetime.now(UTC)
            record = ReminderRecord.model_validate(merged)
            conn = self._get_connection()
            with conn:
                conn.execute(
                    "UPDATE personal_reminders SET message = ?, fire_utc = ?, "
                    "recurrence = ?, max_fires = ?, updated_at = ? WHERE reminder_id = ?;",
                    (
                        record.message,
                        _dt_to_str(record.fire_utc),
                        record.recurrence.value,
                        record.max_fires,
                        _dt_to_str(record.updated_at),
                        reminder_id,
                    ),
                )
        return record

    def cancel_reminder(self, reminder_id: str) -> ReminderRecord:
        """Cancel an active reminder (idempotent when already cancelled)."""
        with self._lock:
            existing = self.get_reminder(reminder_id)
            if existing is None:
                raise PersonalNotFoundError(f"reminder '{reminder_id}' not found")
            if existing.status == ReminderStatus.CANCELLED:
                return existing
            if existing.status == ReminderStatus.COMPLETED:
                raise PersonalStoreError(
                    f"reminder '{reminder_id}' already completed and cannot be cancelled"
                )
            now = datetime.now(UTC)
            conn = self._get_connection()
            with conn:
                conn.execute(
                    "UPDATE personal_reminders SET status = ?, updated_at = ? "
                    "WHERE reminder_id = ? AND status = ?;",
                    (
                        ReminderStatus.CANCELLED.value,
                        _dt_to_str(now),
                        reminder_id,
                        ReminderStatus.ACTIVE.value,
                    ),
                )
            record = self.get_reminder(reminder_id)
        logger.info("personal_reminder_cancelled", reminder_id=reminder_id)
        assert record is not None
        return record

    def delete_reminder(self, reminder_id: str) -> bool:
        """Permanently remove a reminder row; returns False when absent."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                cur = conn.execute(
                    "DELETE FROM personal_reminders WHERE reminder_id = ?;", (reminder_id,)
                )
            deleted = cur.rowcount > 0
        if deleted:
            logger.info("personal_reminder_deleted", reminder_id=reminder_id)
        return deleted

    def due_reminders(self, now: datetime) -> list[ReminderRecord]:
        """Return active reminders whose next fire time has arrived."""
        with self._lock:
            conn = self._get_connection()
            rows = conn.execute(
                "SELECT * FROM personal_reminders WHERE status = ? AND fire_utc <= ? "
                "ORDER BY fire_utc ASC;",
                (ReminderStatus.ACTIVE.value, _dt_to_str(now)),
            ).fetchall()
        return [self._row_to_reminder(row) for row in rows]

    def record_reminder_fire(
        self, reminder_id: str, expected_fires: int, fired_at: datetime
    ) -> ReminderRecord | None:
        """Atomically consume one firing of a reminder occurrence.

        Uses an optimistic ``fires`` guard so a duplicate concurrent fire is
        suppressed at the database level: when the stored ``fires`` counter no
        longer matches ``expected_fires`` (or the reminder is no longer active),
        ``None`` is returned and nothing is written.

        On success the counter and ``last_fired_at`` advance; recurring
        reminders compute their next occurrence from the previous schedule
        (no drift), and the reminder transitions to ``completed`` when the
        bounded firing budget or one-shot model is exhausted.
        """
        with self._lock:
            existing = self.get_reminder(reminder_id)
            if existing is None:
                raise PersonalNotFoundError(f"reminder '{reminder_id}' not found")
            if existing.status != ReminderStatus.ACTIVE or existing.fires != expected_fires:
                return None

            new_fires = existing.fires + 1
            next_fire = advance_occurrence(existing.fire_utc, existing.recurrence)
            if new_fires >= existing.max_fires or next_fire is None:
                completed = True
            else:
                completed = False

            status = ReminderStatus.COMPLETED if completed else ReminderStatus.ACTIVE
            next_fire_str = _dt_to_str(next_fire if next_fire is not None else existing.fire_utc)
            now_str = _dt_to_str(datetime.now(UTC))
            conn = self._get_connection()
            with conn:
                cur = conn.execute(
                    "UPDATE personal_reminders SET fires = ?, fire_utc = ?, "
                    "last_fired_at = ?, status = ?, updated_at = ? "
                    "WHERE reminder_id = ? AND fires = ? AND status = ?;",
                    (
                        new_fires,
                        next_fire_str,
                        _dt_to_str(fired_at),
                        status.value,
                        now_str,
                        reminder_id,
                        expected_fires,
                        ReminderStatus.ACTIVE.value,
                    ),
                )
                if cur.rowcount == 0:
                    return None
            record = self.get_reminder(reminder_id)
        logger.info(
            "personal_reminder_fired",
            reminder_id=reminder_id,
            fires=new_fires,
            status=status.value,
        )
        assert record is not None
        return record


__all__ = [
    "PERSONAL_SCHEMA_VERSION",
    "PersonalNotFoundError",
    "PersonalStore",
    "PersonalStoreError",
    "advance_occurrence",
    "default_max_fires",
]
