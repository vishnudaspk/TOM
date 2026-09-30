"""Unit tests for MemoryManager core orchestrator.

Adheres to:
- PHASE5_IMPLEMENTATIONPLAN.md (Iterations 1 & 2)
- Decision 037: MemoryManager as Single Entry Point & SQLite Source of Truth
- Decision 038: CPU-Only Embeddings & Optional Qdrant Semantic Fallback
- skills/python/memory-system
- skills/testing/python-testing
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tom.memory.embeddings import MockEmbeddingProvider
from tom.memory.manager import MemoryManager
from tom.memory.qdrant import QdrantMemory
from tom.memory.sqlite import SQLiteMemory
from tom.schemas.config import MemoryConfig
from tom.schemas.memory import (
    MemoryImportance,
    MemoryQuery,
    MemoryRecord,
    MemoryType,
)


@pytest.fixture
def memory_manager(tmp_path: Path) -> MemoryManager:
    """Fixture providing an isolated MemoryManager instance with in-memory Qdrant and mock embedder."""
    db_file = str(tmp_path / "test_manager.db")
    config = MemoryConfig(sqlite_db_path=db_file, enable_semantic_search=True)
    sqlite_store = SQLiteMemory(db_path=db_file)
    embedder = MockEmbeddingProvider(dimension=384)
    qdrant_store = QdrantMemory(host=":memory:", vector_size=384)
    manager = MemoryManager(
        config=config,
        sqlite_store=sqlite_store,
        embedding_provider=embedder,
        qdrant_store=qdrant_store,
    )
    yield manager


def test_manager_store_and_get(memory_manager: MemoryManager):
    """Verify async store and retrieval via MemoryManager."""

    async def _test():
        rec = MemoryRecord(
            memory_id="mgr-1",
            type=MemoryType.SEMANTIC,
            content="TOM is a local-first desktop AI assistant.",
            importance=MemoryImportance.IMPORTANT,
            user_confirmed=True,
        )
        saved_id = await memory_manager.store(rec)
        assert saved_id == "mgr-1"

        retrieved = await memory_manager.get("mgr-1")
        assert retrieved is not None
        assert retrieved.memory_id == "mgr-1"
        assert retrieved.content == "TOM is a local-first desktop AI assistant."
        assert retrieved.importance == MemoryImportance.IMPORTANT

        # Vector should also be indexed in Qdrant
        assert "mgr-1" in memory_manager.qdrant._memory_vectors

        assert await memory_manager.get("non-existent") is None

    asyncio.run(_test())


def test_manager_update(memory_manager: MemoryManager):
    """Verify async update via MemoryManager."""

    async def _test():
        rec = MemoryRecord(
            memory_id="mgr-update-1",
            type=MemoryType.TASK,
            content="Draft initial implementation",
        )
        await memory_manager.store(rec)

        ok = await memory_manager.update(
            "mgr-update-1",
            content="Completed initial implementation",
            importance=MemoryImportance.CRITICAL,
        )
        assert ok is True

        updated = await memory_manager.get("mgr-update-1")
        assert updated is not None
        assert updated.content == "Completed initial implementation"
        assert updated.importance == MemoryImportance.CRITICAL

    asyncio.run(_test())


def test_manager_delete_and_forget(memory_manager: MemoryManager):
    """Verify async delete and forget removes from both SQLite and Qdrant."""

    async def _test():
        r1 = MemoryRecord(memory_id="mgr-del-1", type=MemoryType.EPISODIC, content="Log 1")
        r2 = MemoryRecord(memory_id="mgr-del-2", type=MemoryType.EPISODIC, content="Log 2")
        await memory_manager.store(r1)
        await memory_manager.store(r2)

        assert "mgr-del-1" in memory_manager.qdrant._memory_vectors
        assert "mgr-del-2" in memory_manager.qdrant._memory_vectors

        assert await memory_manager.delete("mgr-del-1") is True
        assert await memory_manager.get("mgr-del-1") is None
        assert "mgr-del-1" not in memory_manager.qdrant._memory_vectors

        assert await memory_manager.forget("mgr-del-2") is True
        assert await memory_manager.get("mgr-del-2") is None
        assert "mgr-del-2" not in memory_manager.qdrant._memory_vectors

    asyncio.run(_test())


def test_manager_hybrid_search_rrf(memory_manager: MemoryManager):
    """Verify hybrid search with Reciprocal Rank Fusion ranks relevant results."""

    async def _test():
        r1 = MemoryRecord(
            memory_id="m-search-1",
            type=MemoryType.PREFERENCE,
            content="User prefers Python for backend scripting and data science.",
            importance=MemoryImportance.IMPORTANT,
            user_confirmed=True,
        )
        r2 = MemoryRecord(
            memory_id="m-search-2",
            type=MemoryType.TASK,
            content="Optimize database query performance in SQL.",
            importance=MemoryImportance.USEFUL,
        )
        r3 = MemoryRecord(
            memory_id="m-search-3",
            type=MemoryType.SEMANTIC,
            content="Python features dynamic typing and expressive syntax.",
            importance=MemoryImportance.CRITICAL,
            user_confirmed=True,
        )
        await memory_manager.store(r1)
        await memory_manager.store(r2)
        await memory_manager.store(r3)

        query = MemoryQuery(text="Python backend scripting", limit=5)
        results = await memory_manager.search(query)
        assert len(results) >= 2
        # Fused top score should match the exact relevance
        top_ids = [res.record.memory_id for res in results]
        assert "m-search-1" in top_ids
        assert "m-search-2" not in top_ids
        assert all(0.0 <= res.score <= 1.0 for res in results)

    asyncio.run(_test())


def test_manager_authoritative_sqlite_truth_filters_stale_vectors(memory_manager: MemoryManager):
    """Verify stale vectors in Qdrant without corresponding SQLite record are excluded."""

    async def _test():
        # Inject vector directly into Qdrant that is NOT in SQLite
        fake_vec = await memory_manager.embeddings.embed("Ghost memory")
        await memory_manager.qdrant.upsert("ghost-id", fake_vec)

        query = MemoryQuery(text="Ghost memory", limit=5)
        results = await memory_manager.search(query)
        # SQLite is authoritative: ghost-id must NOT be returned
        assert all(res.record.memory_id != "ghost-id" for res in results)

    asyncio.run(_test())


def test_manager_qdrant_offline_fallback(tmp_path: Path):
    """Verify search and store smoothly fall back to SQLite when Qdrant is unreachable."""

    async def _test():
        db_file = str(tmp_path / "fallback.db")
        config = MemoryConfig(sqlite_db_path=db_file, enable_semantic_search=True)
        sqlite_store = SQLiteMemory(db_path=db_file)
        # Point to unreachable port
        unreachable_qdrant = QdrantMemory(host="127.0.0.1", port=65400)
        embedder = MockEmbeddingProvider(dimension=384)

        manager = MemoryManager(
            config=config,
            sqlite_store=sqlite_store,
            embedding_provider=embedder,
            qdrant_store=unreachable_qdrant,
        )

        rec = MemoryRecord(
            memory_id="fb-1",
            type=MemoryType.SEMANTIC,
            content="Fallback fact should store in SQLite even if Qdrant offline.",
        )
        # Store must succeed in SQLite
        saved_id = await manager.store(rec)
        assert saved_id == "fb-1"
        assert await manager.get("fb-1") is not None

        # Search must fall back to SQLite keyword search without raising exception
        query = MemoryQuery(text="Fallback fact")
        results = await manager.search(query)
        assert len(results) == 1
        assert results[0].record.memory_id == "fb-1"

        await manager.close()

    asyncio.run(_test())


def test_manager_embedding_unhealthy_fallback(tmp_path: Path):
    """Verify search falls back to SQLite keyword search if embedding provider fails."""

    async def _test():
        db_file = str(tmp_path / "unhealthy.db")
        config = MemoryConfig(sqlite_db_path=db_file, enable_semantic_search=True)
        sqlite_store = SQLiteMemory(db_path=db_file)
        qdrant_store = QdrantMemory(host=":memory:", vector_size=384)
        unhealthy_embedder = MockEmbeddingProvider(healthy=False)

        manager = MemoryManager(
            config=config,
            sqlite_store=sqlite_store,
            embedding_provider=unhealthy_embedder,
            qdrant_store=qdrant_store,
        )

        rec = MemoryRecord(
            memory_id="unh-1",
            type=MemoryType.SEMANTIC,
            content="Unhealthy embedder fallback item.",
        )
        # Store writes to SQLite despite embedding error
        await manager.store(rec)
        assert await manager.get("unh-1") is not None

        # Search falls back cleanly
        query = MemoryQuery(text="Unhealthy embedder")
        results = await manager.search(query)
        assert len(results) == 1
        assert results[0].record.memory_id == "unh-1"

        await manager.close()

    asyncio.run(_test())


def test_manager_recent_and_preferences(memory_manager: MemoryManager):
    """Verify async recent and preferences retrieval."""

    async def _test():
        p1 = MemoryRecord(
            memory_id="mgr-pref-1",
            type=MemoryType.PREFERENCE,
            content="Default sound is mute",
        )
        t1 = MemoryRecord(
            memory_id="mgr-task-1",
            type=MemoryType.TASK,
            content="Setup local repository",
        )
        await memory_manager.store(p1)
        await memory_manager.store(t1)

        recent = await memory_manager.recent(limit=10)
        assert len(recent) == 2

        prefs = await memory_manager.preferences()
        assert len(prefs) == 1
        assert prefs[0].memory_id == "mgr-pref-1"

    asyncio.run(_test())


def test_manager_cleanup_expired(memory_manager: MemoryManager):
    """Verify async cleanup_expired purging expired records."""

    async def _test():
        now = datetime.now(UTC)
        active = MemoryRecord(
            memory_id="mgr-active",
            type=MemoryType.TASK,
            content="Active item",
            expires_at=now + timedelta(days=1),
        )
        expired = MemoryRecord(
            memory_id="mgr-expired",
            type=MemoryType.TASK,
            content="Expired item",
            created_at=now - timedelta(days=2),
            expires_at=now - timedelta(hours=1),
        )
        await memory_manager.store(active)
        await memory_manager.store(expired)

        count = await memory_manager.cleanup_expired()
        assert count == 1
        assert await memory_manager.get("mgr-expired") is None
        assert await memory_manager.get("mgr-active") is not None

    asyncio.run(_test())


def test_manager_concurrent_async_operations(memory_manager: MemoryManager):
    """Verify simultaneous async tasks using MemoryManager."""

    async def _test():
        async def insert_worker(idx: int):
            for i in range(10):
                rec = MemoryRecord(
                    memory_id=f"async-worker-{idx}-{i}",
                    type=MemoryType.SEMANTIC,
                    content=f"Knowledge record {idx}-{i}",
                )
                await memory_manager.store(rec)

        async def read_worker():
            for _ in range(10):
                await memory_manager.recent(limit=5)
                await memory_manager.search(MemoryQuery(text="Knowledge"))

        await asyncio.gather(
            insert_worker(1),
            insert_worker(2),
            insert_worker(3),
            read_worker(),
            read_worker(),
        )

        all_recent = await memory_manager.recent(limit=50)
        assert len(all_recent) == 30

        await memory_manager.close()

    asyncio.run(_test())
