---
name: python-memory-system
description: >
  How to implement and use TOM's hybrid memory system (SQLite + Qdrant). Use when building
  the Memory Manager, memory tools, embedding pipeline, or memory policies. Also use when
  deciding what should or shouldn't be stored, or when memory retrieval is returning
  irrelevant or too many results. Do NOT use for generic SQLite or Qdrant usage outside TOM.
---

# Python Memory System

TOM's memory is a controlled, policy-governed subsystem. Agents request memory operations
through a typed interface — they never touch SQLite or Qdrant directly.

---

## Architecture (from plan.md)

```
LLM / Agent
    │
    ▼
Memory Manager (memory/manager.py)  ← single entry point
    │
    ├── SQLite (memory/sqlite.py)   ← source of truth
    └── Qdrant (memory/qdrant.py)   ← semantic retrieval only
```

**SQLite is authoritative.** Qdrant is a retrieval index that references SQLite by `memory_id`.

---

## Memory Record Schema

```python
from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Literal

class MemoryRecord(BaseModel):
    memory_id: int
    memory_type: Literal["ephemeral", "short_term", "long_term", "preference", "task", "event"]
    content: str
    importance: Literal["temporary", "useful", "important", "critical"]
    source: str                          # "conversation", "user_explicit", "task_result"
    user_confirmed: bool = False
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime] = None
    metadata: dict = {}
```

---

## Memory Manager API

The Memory Manager exposes only these typed operations:

```python
class MemoryManager:
    async def search(self, query: str, limit: int = 5) -> list[MemoryRecord]: ...
    async def get(self, memory_id: int) -> Optional[MemoryRecord]: ...
    async def store(self, content: str, memory_type: str, importance: str, ...) -> MemoryRecord: ...
    async def update(self, memory_id: int, content: str) -> MemoryRecord: ...
    async def forget(self, memory_id: int) -> bool: ...
    async def recent(self, limit: int = 10) -> list[MemoryRecord]: ...
    async def preferences(self) -> list[MemoryRecord]: ...
```

Agents call these methods through the tool system — they never call SQLite/Qdrant directly.

---

## What to Remember vs Forget (from plan.md)

**STORE:**
- Stable user preferences (e.g. "User prefers Kokoro for TTS")
- Frequently used settings
- Important project context
- Repeated workflows
- User-approved facts
- Useful task history

**DO NOT STORE:**
- Temporary conversation noise
- One-time commands
- API keys, passwords, tokens, credentials
- Secrets or sensitive credentials
- Information with no foreseeable future value

---

## Memory Policy

Before storing, apply policy checks:

```python
class MemoryPolicy:
    def should_store(self, candidate: MemoryCandidate) -> StorageDecision:
        if self._contains_credentials(candidate.content):
            return StorageDecision.REJECT
        if candidate.importance == "temporary" and not candidate.user_explicit:
            return StorageDecision.REJECT
        return StorageDecision.STORE_LONG_TERM
```

Policy is not applied by the LLM — it is applied by `MemoryManager` before writing.

---

## Embedding and Qdrant Sync

When a memory is stored in SQLite, also embed and index it in Qdrant.

```python
async def store(self, content: str, ...) -> MemoryRecord:
    record = await self.sqlite.insert(content, ...)
    embedding = await self.embeddings.embed(content)
    await self.qdrant.upsert(record.memory_id, embedding, payload={"content": content})
    return record
```

When a memory is deleted, remove it from both stores:

```python
async def forget(self, memory_id: int) -> bool:
    await self.qdrant.delete(memory_id)
    return await self.sqlite.delete(memory_id)
```

---

## Retrieval Strategy

For a user query, search Qdrant for semantic matches, then fetch full records from SQLite:

```python
async def search(self, query: str, limit: int = 5) -> list[MemoryRecord]:
    embedding = await self.embeddings.embed(query)
    hits = await self.qdrant.search(embedding, limit=limit)
    memory_ids = [h.payload["memory_id"] for h in hits]
    return await self.sqlite.get_many(memory_ids)
```

Only return relevant memories — never dump the entire database into an LLM context.

---

## Memory Expiration

Run a periodic cleanup task that removes expired memories:

```python
async def cleanup_expired(self):
    expired = await self.sqlite.get_expired()
    for record in expired:
        await self.forget(record.memory_id)
```

---

## Safety Boundary

The LLM can **request** memory operations through typed tools.
It cannot construct arbitrary SQL queries or directly access Qdrant.

```
LLM → Typed memory tool → Pydantic validation → MemoryManager → SQLite/Qdrant
```

---

## Related Skills

- `python/pydantic-agents` — How agents use memory tools
- `security/permission-model` — Memory access is also permission-controlled
- `testing/python-testing` — How to test memory manager operations
