"""Unit tests for SQLiteMemory authoritative store.

Adheres to:
- PHASE5_IMPLEMENTATIONPLAN.md (Iteration 1)
- skills/python/memory-system
- skills/testing/python-testing
"""

import concurrent.futures
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tom.memory.sqlite import SQLiteMemory
from tom.schemas.memory import (
    MemoryImportance,
    MemoryQuery,
    MemoryRecord,
    MemoryType,
)


@pytest.fixture
def memory_db(tmp_path: Path) -> SQLiteMemory:
    """Fixture providing an isolated file-backed SQLiteMemory store."""
    db_file = tmp_path / "test_tom_memory.db"
    store = SQLiteMemory(db_path=str(db_file))
    yield store
    store.close()


def test_sqlite_store_and_get(memory_db: SQLiteMemory):
    """Verify storing and retrieving a record by ID."""
    rec = MemoryRecord(
        memory_id="mem-1",
        type=MemoryType.PREFERENCE,
        content="Prefers dark mode theme.",
        importance=MemoryImportance.IMPORTANT,
        source="user_explicit",
        user_confirmed=True,
        metadata={"theme": "dark"},
    )
    saved_id = memory_db.store(rec)
    assert saved_id == "mem-1"

    retrieved = memory_db.get("mem-1")
    assert retrieved is not None
    assert retrieved.memory_id == "mem-1"
    assert retrieved.type == MemoryType.PREFERENCE
    assert retrieved.content == "Prefers dark mode theme."
    assert retrieved.importance == MemoryImportance.IMPORTANT
    assert retrieved.source == "user_explicit"
    assert retrieved.user_confirmed is True
    assert retrieved.metadata == {"theme": "dark"}


def test_sqlite_get_nonexistent(memory_db: SQLiteMemory):
    """Verify retrieving a non-existent ID returns None."""
    assert memory_db.get("does-not-exist") is None


def test_sqlite_update(memory_db: SQLiteMemory):
    """Verify updating fields of an existing record."""
    rec = MemoryRecord(
        memory_id="mem-update-1",
        type=MemoryType.TASK,
        content="Initial task description",
        importance=MemoryImportance.USEFUL,
    )
    memory_db.store(rec)

    success = memory_db.update(
        "mem-update-1",
        content="Updated task description",
        importance=MemoryImportance.CRITICAL,
        metadata={"priority": "high"},
    )
    assert success is True

    updated = memory_db.get("mem-update-1")
    assert updated is not None
    assert updated.content == "Updated task description"
    assert updated.importance == MemoryImportance.CRITICAL
    assert updated.metadata == {"priority": "high"}


def test_sqlite_update_nonexistent_and_invalid(memory_db: SQLiteMemory):
    """Verify update on non-existent record or invalid field handling."""
    assert memory_db.update("missing-id", content="test") is False
    assert memory_db.update("missing-id") is False

    with pytest.raises(ValueError, match="Cannot update invalid fields"):
        memory_db.update("any-id", forbidden_field="value")


def test_sqlite_delete_and_forget(memory_db: SQLiteMemory):
    """Verify deleting and forgetting records."""
    rec1 = MemoryRecord(memory_id="del-1", type=MemoryType.EPISODIC, content="Fact 1")
    rec2 = MemoryRecord(memory_id="del-2", type=MemoryType.EPISODIC, content="Fact 2")
    memory_db.store(rec1)
    memory_db.store(rec2)

    assert memory_db.delete("del-1") is True
    assert memory_db.get("del-1") is None
    assert memory_db.delete("del-1") is False

    assert memory_db.forget("del-2") is True
    assert memory_db.get("del-2") is None


def test_sqlite_persistence_across_instances(tmp_path: Path):
    """Verify data persists on disk across SQLiteMemory instances."""
    db_file = str(tmp_path / "persist.db")
    store1 = SQLiteMemory(db_path=db_file)
    store1.store(MemoryRecord(memory_id="p-1", type=MemoryType.SEMANTIC, content="Persistent fact"))
    store1.close()

    store2 = SQLiteMemory(db_path=db_file)
    rec = store2.get("p-1")
    assert rec is not None
    assert rec.content == "Persistent fact"
    store2.close()


def test_sqlite_transactional_rollback(memory_db: SQLiteMemory):
    """Verify failed transactions do not leave partial state."""
    rec = MemoryRecord(memory_id="rb-1", type=MemoryType.EVENT, content="Before error")
    memory_db.store(rec)

    conn = memory_db._get_connection()
    try:
        with conn:
            conn.execute("UPDATE memories SET content = 'Corrupted' WHERE memory_id = 'rb-1';")
            # Force an SQL error to cause rollback
            conn.execute("INSERT INTO nonexistent_table VALUES (1);")
    except Exception:
        pass

    fresh_rec = memory_db.get("rb-1")
    assert fresh_rec is not None
    assert fresh_rec.content == "Before error"


def test_sqlite_keyword_search_and_ranking(memory_db: SQLiteMemory):
    """Verify keyword search, ranking, and limit behavior."""
    r1 = MemoryRecord(
        memory_id="s-1",
        type=MemoryType.SEMANTIC,
        content="Python is an interpreted programming language.",
        importance=MemoryImportance.USEFUL,
    )
    r2 = MemoryRecord(
        memory_id="s-2",
        type=MemoryType.SEMANTIC,
        content="Rust is a systems programming language with memory safety.",
        importance=MemoryImportance.IMPORTANT,
    )
    r3 = MemoryRecord(
        memory_id="s-3",
        type=MemoryType.PREFERENCE,
        content="User loves programming in Rust and Python.",
        importance=MemoryImportance.CRITICAL,
    )
    r4 = MemoryRecord(
        memory_id="s-4",
        type=MemoryType.TASK,
        content="Cook dinner tonight.",
        importance=MemoryImportance.LOW,
    )

    for r in [r1, r2, r3, r4]:
        memory_db.store(r)

    # Search for "programming" -> r1, r2, r3 match; r4 excluded
    q1 = MemoryQuery(text="programming", limit=10)
    res1 = memory_db.search(q1)
    assert len(res1) == 3
    ids = [item.record.memory_id for item in res1]
    assert "s-4" not in ids

    # Exact match scoring
    q2 = MemoryQuery(text="Rust is a systems programming language", limit=10)
    res2 = memory_db.search(q2)
    assert len(res2) > 0
    assert res2[0].record.memory_id == "s-2"
    assert res2[0].score == 1.0

    # Type filtering
    q3 = MemoryQuery(text="programming", types=[MemoryType.PREFERENCE])
    res3 = memory_db.search(q3)
    assert len(res3) == 1
    assert res3[0].record.memory_id == "s-3"

    # Importance filtering (CRITICAL only)
    q4 = MemoryQuery(text="programming", importance_min=MemoryImportance.CRITICAL)
    res4 = memory_db.search(q4)
    assert len(res4) == 1
    assert res4[0].record.memory_id == "s-3"

    # Limit enforcement
    q5 = MemoryQuery(text="programming", limit=2)
    res5 = memory_db.search(q5)
    assert len(res5) == 2


def test_sqlite_expiration_and_cleanup(memory_db: SQLiteMemory):
    """Verify expired memory exclusion from search and cleanup_expired."""
    now = datetime.now(UTC)
    unexpired = MemoryRecord(
        memory_id="exp-alive",
        type=MemoryType.TASK,
        content="Still active task",
        expires_at=now + timedelta(days=5),
    )
    expired = MemoryRecord(
        memory_id="exp-dead",
        type=MemoryType.TASK,
        content="Old expired task",
        expires_at=now - timedelta(minutes=5),
    )
    no_expiry = MemoryRecord(
        memory_id="exp-none",
        type=MemoryType.TASK,
        content="Permanent task",
        expires_at=None,
    )

    for r in [unexpired, expired, no_expiry]:
        memory_db.store(r)

    # Default search excludes expired
    search_res = memory_db.search(MemoryQuery(text="task", include_expired=False))
    found_ids = [res.record.memory_id for res in search_res]
    assert "exp-alive" in found_ids
    assert "exp-none" in found_ids
    assert "exp-dead" not in found_ids

    # Search with include_expired=True
    search_all = memory_db.search(MemoryQuery(text="task", include_expired=True))
    all_ids = [res.record.memory_id for res in search_all]
    assert "exp-dead" in all_ids

    # Cleanup expired
    purged_count = memory_db.cleanup_expired(current_time=now)
    assert purged_count == 1
    assert memory_db.get("exp-dead") is None
    assert memory_db.get("exp-alive") is not None
    assert memory_db.get("exp-none") is not None


def test_sqlite_recent_and_preferences(memory_db: SQLiteMemory):
    """Verify recent and preferences queries."""
    now = datetime.now(UTC)
    p1 = MemoryRecord(
        memory_id="pref-1",
        type=MemoryType.PREFERENCE,
        content="Prefers tabs over spaces",
        created_at=now - timedelta(minutes=10),
    )
    p2 = MemoryRecord(
        memory_id="pref-2",
        type=MemoryType.PREFERENCE,
        content="Prefers pytest for testing",
        created_at=now - timedelta(minutes=5),
    )
    t1 = MemoryRecord(
        memory_id="task-1",
        type=MemoryType.TASK,
        content="Build memory manager",
        created_at=now - timedelta(minutes=1),
    )

    for r in [p1, p2, t1]:
        memory_db.store(r)

    # Recent (ordered by created_at DESC)
    recent_items = memory_db.recent(limit=2)
    assert len(recent_items) == 2
    assert recent_items[0].memory_id == "task-1"
    assert recent_items[1].memory_id == "pref-2"

    # Preferences
    prefs = memory_db.preferences()
    assert len(prefs) == 2
    pref_ids = [p.memory_id for p in prefs]
    assert "pref-1" in pref_ids
    assert "pref-2" in pref_ids
    assert "task-1" not in pref_ids


def test_sqlite_concurrency_and_wal(tmp_path: Path):
    """Verify concurrent reads and writes in WAL mode without locking exceptions."""
    db_file = str(tmp_path / "concurrent.db")
    store = SQLiteMemory(db_path=db_file)

    def write_worker(idx: int):
        for i in range(15):
            rec = MemoryRecord(
                memory_id=f"worker-{idx}-{i}",
                type=MemoryType.EPISODIC,
                content=f"Worker {idx} iteration {i} content",
                importance=MemoryImportance.USEFUL,
            )
            store.store(rec)

    def read_worker():
        for _ in range(15):
            store.recent(limit=5)
            store.search(MemoryQuery(text="Worker", limit=5))

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        write_futures = [executor.submit(write_worker, i) for i in range(3)]
        read_futures = [executor.submit(read_worker) for _ in range(3)]
        concurrent.futures.wait(write_futures + read_futures)

    # Check that all records were written
    all_recent = store.recent(limit=100)
    assert len(all_recent) == 45
    store.close()
