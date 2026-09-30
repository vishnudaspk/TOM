"""Optional Qdrant semantic vector index for TOM memory subsystem.

Adheres to:
- PHASE5_IMPLEMENTATIONPLAN.md (Iteration 2)
- Decision 038: CPU-Only Embeddings & Optional Qdrant Semantic Fallback
- skills/python/memory-system: SQLite is authoritative; Qdrant is an optional retrieval index.
- Critical Invariant: No plaintext memory content is stored in Qdrant (only memory_id reference).
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("tom.memory.qdrant")


class QdrantMemory:
    """Optional vector retrieval layer using Qdrant REST API or in-memory simulation."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6333,
        collection: str = "tom_memories",
        vector_size: int = 384,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.collection = collection
        self.vector_size = vector_size
        self._in_memory = host == ":memory:"
        self._memory_vectors: dict[str, list[float]] = {}
        self._custom_client = http_client is not None
        if self._in_memory:
            self._http_client = None
        else:
            self._http_client = http_client or httpx.AsyncClient(
                base_url=f"http://{host}:{port}",
                timeout=0.5,
            )
        self._collection_ensured = False

    async def is_available(self) -> bool:
        """Check if Qdrant service is reachable and responsive."""
        if self._in_memory:
            return True

        if self._http_client is None:
            return False

        try:
            resp = await self._http_client.get("/readyz", timeout=0.2)
            return resp.status_code == 200
        except Exception:
            try:
                resp = await self._http_client.get("/collections", timeout=0.2)
                return resp.status_code == 200
            except Exception:
                return False

    async def ensure_collection(self) -> bool:
        """Ensure that the vector collection exists with the configured dimension and distance."""
        if self._in_memory:
            self._collection_ensured = True
            return True

        if self._collection_ensured:
            return True

        if self._http_client is None:
            return False

        try:
            resp = await self._http_client.get(f"/collections/{self.collection}")
            if resp.status_code == 200:
                self._collection_ensured = True
                return True

            if resp.status_code == 404:
                create_resp = await self._http_client.put(
                    f"/collections/{self.collection}",
                    json={
                        "vectors": {
                            "size": self.vector_size,
                            "distance": "Cosine",
                        }
                    },
                )
                if create_resp.status_code == 200:
                    self._collection_ensured = True
                    return True
            return False
        except Exception as err:
            logger.debug("Failed to ensure Qdrant collection: %s", err)
            return False

    async def upsert(self, memory_id: str, vector: list[float]) -> bool:
        """Upsert a vector reference keyed by memory_id into Qdrant."""
        if len(vector) != self.vector_size:
            logger.warning(
                "Vector size mismatch: expected %d, got %d",
                self.vector_size,
                len(vector),
            )
            return False

        if self._in_memory:
            self._memory_vectors[memory_id] = list(vector)
            return True

        if self._http_client is None:
            return False

        try:
            if not await self.ensure_collection():
                return False

            # Critical invariant: Payload contains ONLY memory_id reference, NO plaintext content
            payload = {
                "points": [
                    {
                        "id": memory_id,
                        "vector": vector,
                        "payload": {"memory_id": memory_id},
                    }
                ]
            }

            resp = await self._http_client.put(
                f"/collections/{self.collection}/points?wait=true",
                json=payload,
            )
            return resp.status_code == 200
        except Exception as err:
            logger.debug("Qdrant upsert failed for %s: %s", memory_id, err)
            return False

    async def search(
        self,
        vector: list[float],
        top_k: int = 10,
    ) -> list[tuple[str, float]]:
        """Search nearest vector neighbors, returning list of (memory_id, cosine_score) tuples."""
        if top_k <= 0:
            return []

        if self._in_memory:
            if not self._memory_vectors:
                return []
            scores: list[tuple[str, float]] = []
            for mem_id, v in self._memory_vectors.items():
                dot = sum(a * b for a, b in zip(vector, v, strict=False))
                if dot > 0.05:  # Relevance threshold for positive similarity
                    scores.append((mem_id, dot))
            scores.sort(key=lambda item: item[1], reverse=True)
            return scores[:top_k]

        if self._http_client is None:
            return []

        try:
            if not await self.ensure_collection():
                return []

            resp = await self._http_client.post(
                f"/collections/{self.collection}/points/search",
                json={
                    "vector": vector,
                    "limit": top_k,
                    "with_payload": True,
                },
            )
            if resp.status_code != 200:
                return []

            data = resp.json()
            results: list[tuple[str, float]] = []
            for point in data.get("result", []):
                mem_id = point.get("payload", {}).get("memory_id") or str(point.get("id"))
                score = float(point.get("score", 0.0))
                if score > 0.05:
                    results.append((mem_id, score))
            return results
        except Exception as err:
            logger.debug("Qdrant search failed: %s", err)
            return []

    async def delete(self, memory_id: str) -> bool:
        """Delete a vector point by memory_id."""
        if self._in_memory:
            self._memory_vectors.pop(memory_id, None)
            return True

        if self._http_client is None:
            return False

        try:
            resp = await self._http_client.post(
                f"/collections/{self.collection}/points/delete?wait=true",
                json={"points": [memory_id]},
            )
            return resp.status_code == 200
        except Exception as err:
            logger.debug("Qdrant delete failed for %s: %s", memory_id, err)
            return False

    async def close(self) -> None:
        """Close HTTP client session."""
        if not self._custom_client and self._http_client is not None:
            await self._http_client.aclose()


__all__ = ["QdrantMemory"]
