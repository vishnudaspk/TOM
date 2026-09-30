"""Authoritative SQLite memory store for TOM.

Adheres to:
- PHASE5_IMPLEMENTATIONPLAN.md (Iteration 1)
- Decision 037: MemoryManager as Single Entry Point & SQLite Source of Truth
- skills/coding/validation: Strict bounds, timezone-aware datetimes, Pydantic v2 validation.
- skills/python/memory-system: SQLite is authoritative source of truth.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tom.schemas.memory import (
    IMPORTANCE_WEIGHTS,
    MemoryImportance,
    MemoryQuery,
    MemoryRecord,
    MemorySearchResult,
    MemoryType,
)

logger = logging.getLogger("tom.memory.sqlite")


class SQLiteMemory:
    """Authoritative relational SQLite storage for memory records."""

    def __init__(self, db_path: str | Path = "data/memory/tom.db") -> None:
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create the SQLite connection."""
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

            # Apply performance and consistency pragmas
            if self.db_path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute("PRAGMA busy_timeout=5000;")
            self._conn.execute("PRAGMA foreign_keys=ON;")

        return self._conn

    def _init_db(self) -> None:
        """Initialize SQLite database schema and indexes."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS memories (
                        memory_id TEXT PRIMARY KEY,
                        type TEXT NOT NULL,
                        content TEXT NOT NULL,
                        importance TEXT NOT NULL,
                        importance_weight INTEGER NOT NULL,
                        source TEXT,
                        user_confirmed INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        expires_at TEXT,
                        metadata TEXT NOT NULL DEFAULT '{}'
                    );
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_memories_importance_weight "
                    "ON memories(importance_weight);"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at);"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_memories_expires_at ON memories(expires_at);"
                )

    def _row_to_record(self, row: sqlite3.Row) -> MemoryRecord:
        """Convert a database row into a validated MemoryRecord."""
        created_at = datetime.fromisoformat(row["created_at"])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        updated_at = datetime.fromisoformat(row["updated_at"])
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)

        expires_at = None
        if row["expires_at"]:
            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)

        metadata = json.loads(row["metadata"]) if row["metadata"] else {}

        return MemoryRecord(
            memory_id=row["memory_id"],
            type=MemoryType(row["type"]),
            content=row["content"],
            importance=MemoryImportance(row["importance"]),
            source=row["source"],
            user_confirmed=bool(row["user_confirmed"]),
            created_at=created_at,
            updated_at=updated_at,
            expires_at=expires_at,
            metadata=metadata,
        )

    def store(self, record: MemoryRecord) -> str:
        """Store or replace a memory record authoritatively."""
        weight = IMPORTANCE_WEIGHTS[record.importance]
        metadata_json = json.dumps(record.metadata)
        created_str = record.created_at.isoformat()
        updated_str = record.updated_at.isoformat()
        expires_str = record.expires_at.isoformat() if record.expires_at else None

        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    """
                    INSERT INTO memories (
                        memory_id, type, content, importance, importance_weight,
                        source, user_confirmed, created_at, updated_at, expires_at, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(memory_id) DO UPDATE SET
                        type=excluded.type,
                        content=excluded.content,
                        importance=excluded.importance,
                        importance_weight=excluded.importance_weight,
                        source=excluded.source,
                        user_confirmed=excluded.user_confirmed,
                        updated_at=excluded.updated_at,
                        expires_at=excluded.expires_at,
                        metadata=excluded.metadata;
                    """,
                    (
                        record.memory_id,
                        record.type.value,
                        record.content,
                        record.importance.value,
                        weight,
                        record.source,
                        int(record.user_confirmed),
                        created_str,
                        updated_str,
                        expires_str,
                        metadata_json,
                    ),
                )
        return record.memory_id

    def get(self, memory_id: str) -> MemoryRecord | None:
        """Retrieve a memory record by ID."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.execute(
                """
                SELECT memory_id, type, content, importance, source,
                       user_confirmed, created_at, updated_at, expires_at, metadata
                FROM memories
                WHERE memory_id = ?;
                """,
                (memory_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_record(row)

    def update(self, memory_id: str, **fields: Any) -> bool:
        """Update selected fields of an existing memory record."""
        if not fields:
            return False

        allowed_fields = {
            "type",
            "content",
            "importance",
            "source",
            "user_confirmed",
            "updated_at",
            "expires_at",
            "metadata",
        }

        invalid = set(fields.keys()) - allowed_fields
        if invalid:
            raise ValueError(f"Cannot update invalid fields: {invalid}")

        with self._lock:
            conn = self._get_connection()
            current = self.get(memory_id)
            if current is None:
                return False

            updates: list[str] = []
            params: list[Any] = []

            for key, val in fields.items():
                if key == "type":
                    mtype = MemoryType(val)
                    updates.append("type = ?")
                    params.append(mtype.value)
                elif key == "importance":
                    mimp = MemoryImportance(val)
                    updates.append("importance = ?")
                    params.append(mimp.value)
                    updates.append("importance_weight = ?")
                    params.append(IMPORTANCE_WEIGHTS[mimp])
                elif key == "content":
                    if not isinstance(val, str) or not val.strip():
                        raise ValueError("content cannot be empty")
                    updates.append("content = ?")
                    params.append(val)
                elif key == "source":
                    updates.append("source = ?")
                    params.append(val)
                elif key == "user_confirmed":
                    updates.append("user_confirmed = ?")
                    params.append(int(bool(val)))
                elif key == "expires_at":
                    if val is None:
                        updates.append("expires_at = NULL")
                    else:
                        dt = val if isinstance(val, datetime) else datetime.fromisoformat(str(val))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=UTC)
                        updates.append("expires_at = ?")
                        params.append(dt.isoformat())
                elif key == "metadata":
                    if not isinstance(val, dict):
                        raise ValueError("metadata must be a dictionary")
                    updates.append("metadata = ?")
                    params.append(json.dumps(val))
                elif key == "updated_at":
                    dt = val if isinstance(val, datetime) else datetime.fromisoformat(str(val))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    updates.append("updated_at = ?")
                    params.append(dt.isoformat())

            if "updated_at" not in fields:
                updates.append("updated_at = ?")
                params.append(datetime.now(UTC).isoformat())

            params.append(memory_id)
            sql = f"UPDATE memories SET {', '.join(updates)} WHERE memory_id = ?;"

            with conn:
                cur = conn.execute(sql, tuple(params))
                return cur.rowcount > 0

    def delete(self, memory_id: str) -> bool:
        """Delete a memory record by ID."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                cur = conn.execute(
                    "DELETE FROM memories WHERE memory_id = ?;",
                    (memory_id,),
                )
                return cur.rowcount > 0

    def forget(self, memory_id: str) -> bool:
        """Alias for delete to match memory interface semantics."""
        return self.delete(memory_id)

    def recent(self, limit: int = 10, include_expired: bool = False) -> list[MemoryRecord]:
        """Retrieve most recent memories ordered by creation time descending."""
        now_str = datetime.now(UTC).isoformat()
        with self._lock:
            conn = self._get_connection()
            if include_expired:
                sql = """
                    SELECT memory_id, type, content, importance, source,
                           user_confirmed, created_at, updated_at, expires_at, metadata
                    FROM memories
                    ORDER BY created_at DESC
                    LIMIT ?;
                """
                cursor = conn.execute(sql, (limit,))
            else:
                sql = """
                    SELECT memory_id, type, content, importance, source,
                           user_confirmed, created_at, updated_at, expires_at, metadata
                    FROM memories
                    WHERE expires_at IS NULL OR expires_at >= ?
                    ORDER BY created_at DESC
                    LIMIT ?;
                """
                cursor = conn.execute(sql, (now_str, limit))

            return [self._row_to_record(row) for row in cursor.fetchall()]

    def preferences(self, include_expired: bool = False) -> list[MemoryRecord]:
        """Retrieve all stored user preferences."""
        now_str = datetime.now(UTC).isoformat()
        with self._lock:
            conn = self._get_connection()
            if include_expired:
                sql = """
                    SELECT memory_id, type, content, importance, source,
                           user_confirmed, created_at, updated_at, expires_at, metadata
                    FROM memories
                    WHERE type = ?
                    ORDER BY created_at DESC;
                """
                cursor = conn.execute(sql, (MemoryType.PREFERENCE.value,))
            else:
                sql = """
                    SELECT memory_id, type, content, importance, source,
                           user_confirmed, created_at, updated_at, expires_at, metadata
                    FROM memories
                    WHERE type = ? AND (expires_at IS NULL OR expires_at >= ?)
                    ORDER BY created_at DESC;
                """
                cursor = conn.execute(sql, (MemoryType.PREFERENCE.value, now_str))

            return [self._row_to_record(row) for row in cursor.fetchall()]

    def cleanup_expired(self, current_time: datetime | None = None) -> int:
        """Delete all expired memories and return count of deleted records."""
        now = current_time or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        now_str = now.isoformat()

        with self._lock:
            conn = self._get_connection()
            with conn:
                cur = conn.execute(
                    "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at < ?;",
                    (now_str,),
                )
                return cur.rowcount

    def search(self, query: MemoryQuery) -> list[MemorySearchResult]:
        """Perform keyword search with filters and ranking."""
        now_str = datetime.now(UTC).isoformat()
        conditions: list[str] = []
        params: list[Any] = []

        if not query.include_expired:
            conditions.append("(expires_at IS NULL OR expires_at >= ?)")
            params.append(now_str)

        if query.types:
            placeholders = ",".join("?" for _ in query.types)
            conditions.append(f"type IN ({placeholders})")
            params.extend([t.value for t in query.types])

        if query.importance_min:
            min_weight = IMPORTANCE_WEIGHTS[query.importance_min]
            conditions.append("importance_weight >= ?")
            params.append(min_weight)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
            SELECT memory_id, type, content, importance, source,
                   user_confirmed, created_at, updated_at, expires_at, metadata
            FROM memories
            {where_clause}
            ORDER BY created_at DESC;
        """

        with self._lock:
            conn = self._get_connection()
            cursor = conn.execute(sql, tuple(params))
            rows = cursor.fetchall()

        query_text = query.text.strip().lower()
        results: list[MemorySearchResult] = []

        if not query_text:
            # If no search query text, return recent matching records with max score
            for row in rows[: query.limit]:
                results.append(MemorySearchResult(record=self._row_to_record(row), score=1.0))
            return results

        # Score matching records
        query_tokens = [tok for tok in query_text.split() if tok]

        for row in rows:
            record = self._row_to_record(row)
            content_lower = record.content.lower()
            metadata_str = json.dumps(record.metadata).lower()
            source_str = (record.source or "").lower()

            # Exact match bonus
            if query_text in content_lower:
                score = 1.0
            else:
                matched_tokens = sum(
                    1
                    for tok in query_tokens
                    if tok in content_lower or tok in metadata_str or tok in source_str
                )
                if matched_tokens == 0:
                    continue
                score = round(matched_tokens / len(query_tokens), 4)

            results.append(MemorySearchResult(record=record, score=score))

        # Sort results by score descending, then created_at descending
        results.sort(key=lambda r: (r.score, r.record.created_at), reverse=True)
        return results[: query.limit]

    def close(self) -> None:
        """Close SQLite connection."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


__all__ = ["SQLiteMemory"]
