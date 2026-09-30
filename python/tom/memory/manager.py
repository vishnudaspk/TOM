"""Core MemoryManager orchestrator for TOM memory subsystem.

Adheres to:
- PHASE5_IMPLEMENTATIONPLAN.md (Iterations 1, 2 & 3)
- Decision 037: MemoryManager as Single Entry Point & SQLite Source of Truth
- Decision 038: CPU-Only Embeddings & Optional Qdrant Semantic Fallback
- skills/python/memory-system: Strict decoupling from LLMs, agents, and prompts.
- Invariant: MemoryPolicy evaluation -> SQLite authoritative store -> optional Qdrant semantic index.
- Invariant: SQLite is authoritative; Qdrant stores only vector indices, not plaintext.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from tom.memory.embeddings import CPUEmbeddingProvider, EmbeddingProvider
from tom.memory.policies import (
    MemoryPolicy,
    MemoryPolicyViolationError,
    PolicyDecision,
    SecretDetectedError,
)
from tom.memory.qdrant import QdrantMemory
from tom.memory.sqlite import SQLiteMemory
from tom.schemas.config import MemoryConfig
from tom.schemas.memory import (
    IMPORTANCE_WEIGHTS,
    MemoryQuery,
    MemoryRecord,
    MemorySearchResult,
)

logger = logging.getLogger("tom.memory.manager")


class MemoryManager:
    """Unified memory manager coordinating policy enforcement, authoritative storage, and vector indexing."""

    def __init__(
        self,
        config: MemoryConfig | None = None,
        sqlite_store: SQLiteMemory | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        qdrant_store: QdrantMemory | None = None,
        policy: MemoryPolicy | None = None,
    ) -> None:
        self.config = config or MemoryConfig()
        self.sqlite = sqlite_store or SQLiteMemory(db_path=self.config.sqlite_db_path)
        self.embeddings = embedding_provider or CPUEmbeddingProvider(
            model_name=self.config.embedding_model,
        )
        self.qdrant = qdrant_store or QdrantMemory(
            host=self.config.qdrant_host,
            port=self.config.qdrant_port,
            vector_size=self.embeddings.dimension,
        )
        self.policy = policy or MemoryPolicy()

    async def store(self, record: MemoryRecord) -> str:
        """Validate record against MemoryPolicy, then store authoritatively in SQLite and index in Qdrant."""
        # 0. Deterministic policy evaluation BEFORE any persistence
        evaluation = self.policy.evaluate(record)
        if evaluation.decision == PolicyDecision.REJECT:
            if evaluation.matched_secret:
                raise SecretDetectedError(f"Memory rejected: secret detected ({evaluation.reason})")
            raise MemoryPolicyViolationError(f"Memory rejected by policy: {evaluation.reason}")

        if evaluation.decision == PolicyDecision.REQUIRE_CONFIRMATION and not record.user_confirmed:
            raise MemoryPolicyViolationError(
                f"Memory requires user confirmation before storage ({evaluation.reason})"
            )

        # 1. Authoritative write to SQLite
        memory_id = await asyncio.to_thread(self.sqlite.store, record)

        # 2. Optional semantic vector indexing
        if self.config.enable_semantic_search:
            try:
                if await self.qdrant.is_available():
                    vector = await self.embeddings.embed(record.content)
                    # Critical invariant: upsert passes ONLY memory_id reference, NO plaintext
                    await self.qdrant.upsert(memory_id, vector)
            except Exception as err:
                logger.warning(
                    "Optional Qdrant vector indexing failed for %s (%s); SQLite store remains authoritative.",
                    memory_id,
                    err,
                )

        return memory_id

    async def get(self, memory_id: str) -> MemoryRecord | None:
        """Retrieve a memory record by ID from authoritative SQLite storage."""
        return await asyncio.to_thread(self.sqlite.get, memory_id)

    async def update(self, memory_id: str, **fields: Any) -> bool:
        """Update fields of an existing memory record in SQLite and update Qdrant if content changes."""
        if "content" in fields:
            if self.policy.scan_for_secrets(fields["content"]):
                raise SecretDetectedError("Memory update rejected: secret detected in content")
        if "metadata" in fields and isinstance(fields["metadata"], dict):
            if self.policy.scan_metadata_for_secrets(fields["metadata"]):
                raise SecretDetectedError("Memory update rejected: secret detected in metadata")

        success = await asyncio.to_thread(self.sqlite.update, memory_id, **fields)
        if not success:
            return False

        # If content updated and semantic search enabled, re-embed and update Qdrant
        if "content" in fields and self.config.enable_semantic_search:
            try:
                if await self.qdrant.is_available():
                    vector = await self.embeddings.embed(fields["content"])
                    await self.qdrant.upsert(memory_id, vector)
            except Exception as err:
                logger.warning(
                    "Qdrant vector update failed for %s (%s); SQLite remains authoritative.",
                    memory_id,
                    err,
                )

        return True

    async def delete(self, memory_id: str) -> bool:
        """Delete a memory record by ID from SQLite and Qdrant."""
        # Remove from optional vector index
        if self.config.enable_semantic_search:
            try:
                await self.qdrant.delete(memory_id)
            except Exception as err:
                logger.debug("Qdrant vector deletion failed for %s: %s", memory_id, err)

        # Remove from authoritative SQLite
        return await asyncio.to_thread(self.sqlite.delete, memory_id)

    async def forget(self, memory_id: str) -> bool:
        """Forget/delete a memory record by ID."""
        return await self.delete(memory_id)

    def _record_matches_query(
        self,
        record: MemoryRecord,
        query: MemoryQuery,
        now: datetime,
    ) -> bool:
        """Validate an authoritative SQLite record against query filter conditions."""
        if not query.include_expired and record.expires_at is not None:
            if record.expires_at < now:
                return False

        if query.types and record.type not in query.types:
            return False

        if query.importance_min:
            min_weight = IMPORTANCE_WEIGHTS[query.importance_min]
            if IMPORTANCE_WEIGHTS[record.importance] < min_weight:
                return False

        return True

    async def search(self, query: MemoryQuery) -> list[MemorySearchResult]:
        """Search memory records using Reciprocal Rank Fusion (RRF) hybrid search or keyword fallback."""
        query_text = query.text.strip()
        if not query_text or not self.config.enable_semantic_search:
            return await asyncio.to_thread(self.sqlite.search, query)

        # Check semantic availability
        semantic_available = False
        try:
            semantic_available = (
                await self.qdrant.is_available() and await self.embeddings.check_health()
            )
        except Exception:
            semantic_available = False

        if not semantic_available:
            logger.debug(
                "Semantic index offline; falling back to authoritative SQLite keyword search."
            )
            return await asyncio.to_thread(self.sqlite.search, query)

        try:
            # 1. Fetch keyword search results from SQLite
            keyword_results = await asyncio.to_thread(self.sqlite.search, query)

            # 2. Fetch semantic candidate IDs from Qdrant
            vector = await self.embeddings.embed(query_text)
            semantic_hits = await self.qdrant.search(vector, top_k=query.limit * 2)

            # 3. Reciprocal Rank Fusion (RRF with k=60)
            rrf_k = 60.0
            fused_scores: dict[str, float] = {}

            # Process keyword ranks
            for rank_idx, res in enumerate(keyword_results):
                mem_id = res.record.memory_id
                fused_scores[mem_id] = fused_scores.get(mem_id, 0.0) + (
                    1.0 / (rrf_k + rank_idx + 1)
                )

            # Process semantic ranks
            for rank_idx, (mem_id, _cos_score) in enumerate(semantic_hits):
                fused_scores[mem_id] = fused_scores.get(mem_id, 0.0) + (
                    1.0 / (rrf_k + rank_idx + 1)
                )

            if not fused_scores:
                return []

            # Normalize fused scores to [0.0, 1.0] relative to theoretical max score (2 / (60 + 1))
            max_theoretical_score = 2.0 / (rrf_k + 1.0)
            scored_candidates = sorted(
                fused_scores.items(),
                key=lambda item: item[1],
                reverse=True,
            )

            now = datetime.now(UTC)
            final_results: list[MemorySearchResult] = []

            for mem_id, raw_score in scored_candidates:
                if len(final_results) >= query.limit:
                    break

                # Resolve against authoritative SQLite store
                record = await asyncio.to_thread(self.sqlite.get, mem_id)
                if record is None:
                    # Stale vector reference or not in SQLite; skip
                    continue

                # Enforce query filters on authoritative record
                if not self._record_matches_query(record, query, now):
                    continue

                normalized_score = min(1.0, max(0.0, round(raw_score / max_theoretical_score, 4)))
                final_results.append(MemorySearchResult(record=record, score=normalized_score))

            return final_results

        except Exception as err:
            logger.warning(
                "Hybrid search failed (%s); falling back cleanly to SQLite keyword search.",
                err,
            )
            return await asyncio.to_thread(self.sqlite.search, query)

    async def recent(self, limit: int = 10) -> list[MemoryRecord]:
        """Retrieve the most recent unexpired memory records."""
        return await asyncio.to_thread(self.sqlite.recent, limit)

    async def preferences(self) -> list[MemoryRecord]:
        """Retrieve all unexpired user preferences."""
        return await asyncio.to_thread(self.sqlite.preferences)

    async def cleanup_expired(self) -> int:
        """Purge all expired records from SQLite and return the count purged."""
        return await asyncio.to_thread(self.sqlite.cleanup_expired)

    async def close(self) -> None:
        """Close underlying storage and index resources."""
        await asyncio.to_thread(self.sqlite.close)
        await self.qdrant.close()


__all__ = ["MemoryManager"]
