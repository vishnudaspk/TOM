"""Unit tests for QdrantMemory vector indexing layer.

Adheres to:
- PHASE5_IMPLEMENTATIONPLAN.md (Iteration 2)
- Decision 038: CPU-Only Embeddings & Optional Qdrant Semantic Fallback
- skills/python/memory-system
- Critical Invariant: No plaintext content in Qdrant (only memory_id).
"""

import asyncio

import httpx
from tom.memory.embeddings import MockEmbeddingProvider
from tom.memory.qdrant import QdrantMemory


def test_qdrant_in_memory_crud():
    """Verify in-memory Qdrant simulation supports upsert, search, and delete."""

    async def _test():
        qdrant = QdrantMemory(host=":memory:", vector_size=384)
        embedder = MockEmbeddingProvider(dimension=384)

        assert await qdrant.is_available() is True
        assert await qdrant.ensure_collection() is True

        # Generate vectors
        v1 = await embedder.embed("Python programming")
        v2 = await embedder.embed("Rust systems development")

        # Upsert
        assert await qdrant.upsert("mem-1", v1) is True
        assert await qdrant.upsert("mem-2", v2) is True

        # Verify critical invariant: vectors are stored with memory_id, no plaintext
        assert "mem-1" in qdrant._memory_vectors
        assert "mem-2" in qdrant._memory_vectors

        # Search for Python
        query_python = await embedder.embed("Python")
        res_python = await qdrant.search(query_python, top_k=5)
        assert len(res_python) >= 1
        assert res_python[0][0] == "mem-1"

        # Search for Rust
        query_rust = await embedder.embed("Rust")
        res_rust = await qdrant.search(query_rust, top_k=5)
        assert len(res_rust) >= 1
        assert res_rust[0][0] == "mem-2"

        # Delete
        assert await qdrant.delete("mem-1") is True
        assert "mem-1" not in qdrant._memory_vectors
        rem_python = await qdrant.search(query_python, top_k=5)
        assert all(item[0] != "mem-1" for item in rem_python)

    asyncio.run(_test())


def test_qdrant_vector_dimension_validation():
    """Verify vectors with incorrect dimensionality are rejected."""

    async def _test():
        qdrant = QdrantMemory(host=":memory:", vector_size=384)
        short_vector = [0.1] * 128
        assert await qdrant.upsert("mem-invalid", short_vector) is False

    asyncio.run(_test())


def test_qdrant_offline_fallback():
    """Verify Qdrant gracefully handles an unreachable remote service."""

    async def _test():
        # Target an unreachable local port with immediate timeout
        qdrant = QdrantMemory(host="127.0.0.1", port=65432, collection="test_col")
        try:
            assert await qdrant.is_available() is False
            assert await qdrant.ensure_collection() is False
            assert await qdrant.upsert("mem-1", [0.0] * 384) is False
            assert await qdrant.search([0.0] * 384, top_k=5) == []
            assert await qdrant.delete("mem-1") is False
        finally:
            await qdrant.close()

    asyncio.run(_test())


def test_qdrant_http_mock_payload_invariant():
    """Verify HTTP client payloads contain ONLY memory_id and zero plaintext."""

    async def _test():
        recorded_requests: list[httpx.Request] = []

        def mock_handler(request: httpx.Request) -> httpx.Response:
            recorded_requests.append(request)
            if request.url.path == "/readyz":
                return httpx.Response(200, json={"status": "ok"})
            if request.url.path == "/collections/test_collection":
                if request.method == "GET":
                    return httpx.Response(200, json={"result": {"status": "green"}})
                if request.method == "PUT":
                    return httpx.Response(200, json={"result": True})
            if request.url.path == "/collections/test_collection/points":
                return httpx.Response(200, json={"result": {"status": "completed"}})
            if request.url.path == "/collections/test_collection/points/search":
                return httpx.Response(
                    200,
                    json={
                        "result": [
                            {"id": "mem-100", "score": 0.95, "payload": {"memory_id": "mem-100"}}
                        ]
                    },
                )
            if request.url.path == "/collections/test_collection/points/delete":
                return httpx.Response(200, json={"result": {"status": "completed"}})
            return httpx.Response(404)

        transport = httpx.MockTransport(mock_handler)
        mock_client = httpx.AsyncClient(transport=transport, base_url="http://mock-qdrant:6333")

        qdrant = QdrantMemory(
            host="mock-qdrant",
            port=6333,
            collection="test_collection",
            vector_size=384,
            http_client=mock_client,
        )

        try:
            assert await qdrant.is_available() is True
            assert await qdrant.upsert("mem-100", [0.1] * 384) is True

            # Check recorded upsert request body for plaintext absence
            upsert_req = [
                r for r in recorded_requests if "/points" in r.url.path and r.method == "PUT"
            ][0]
            body = upsert_req.read().decode("utf-8")
            assert "mem-100" in body
            assert "payload" in body
            assert "content" not in body  # Plaintext content MUST NOT be in Qdrant

            # Search test
            search_res = await qdrant.search([0.1] * 384, top_k=1)
            assert len(search_res) == 1
            assert search_res[0] == ("mem-100", 0.95)

            # Delete test
            assert await qdrant.delete("mem-100") is True
        finally:
            await mock_client.aclose()

    asyncio.run(_test())
