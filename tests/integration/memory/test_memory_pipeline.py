"""End-to-end integration tests for TOM'"'"'s Phase 5 memory pipeline.

These tests keep the full pipeline deterministic and offline:
    user request -> IntentRouter -> Agent/MockModelProvider -> ToolExecutor
    -> PermissionEngine/ConfirmationHook -> MemoryManager -> SQLite/Qdrant.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from tom.agents.base import Agent
from tom.agents.dependencies import AgentDependencies
from tom.agents.orchestrator import AgentOrchestrator
from tom.core.router import IntentRouter
from tom.memory.embeddings import MockEmbeddingProvider
from tom.memory.manager import MemoryManager
from tom.memory.qdrant import QdrantMemory
from tom.memory.sqlite import SQLiteMemory
from tom.models.providers.mock import MockModelProvider
from tom.models.schemas import ModelResponse, ToolCallRequest
from tom.schemas.config import MemoryConfig
from tom.schemas.memory import MemoryImportance, MemoryQuery, MemoryRecord, MemoryType
from tom.schemas.router import IntentDomain
from tom.security.confirmation import (
    AlwaysAllowConfirmationHook,
    AlwaysDenyConfirmationHook,
)
from tom.security.permissions import PermissionEngine
from tom.tools.bootstrap import setup_default_tools
from tom.tools.executor import ToolExecutor
from tom.tools.registry import ToolRegistry


def run_async(coro: Any) -> Any:
    return asyncio.run(coro)


def tool_call(name: str, arguments: dict[str, Any], call_id: str) -> ModelResponse:
    return ModelResponse(
        content="",
        tool_calls=[ToolCallRequest(id=call_id, name=name, arguments=arguments)],
        finish_reason="tool_call",
    )


def make_memory_manager(
    db_path: Path,
    *,
    enable_semantic_search: bool = True,
    qdrant_store: QdrantMemory | None = None,
) -> MemoryManager:
    config = MemoryConfig(
        sqlite_db_path=str(db_path),
        enable_semantic_search=enable_semantic_search,
    )
    sqlite_store = SQLiteMemory(db_path=str(db_path))
    embedder = MockEmbeddingProvider(dimension=384)
    qdrant = qdrant_store or QdrantMemory(host=":memory:", vector_size=384)
    return MemoryManager(
        config=config,
        sqlite_store=sqlite_store,
        embedding_provider=embedder,
        qdrant_store=qdrant,
    )


def make_memory_agent(
    manager: MemoryManager,
    provider: MockModelProvider,
    *,
    confirmation_hook: Any | None = None,
) -> Agent:
    registry = ToolRegistry()
    setup_default_tools(registry=registry, memory_manager=manager)
    executor = ToolExecutor(
        registry=registry,
        permission_engine=PermissionEngine(),
        confirmation_hook=confirmation_hook or AlwaysAllowConfirmationHook(),
    )
    deps = AgentDependencies(
        registry=registry,
        executor=executor,
        model_provider=provider,
        memory_manager=manager,
    )
    return Agent(dependencies=deps)


def make_orchestrator(agent: Agent, provider: MockModelProvider) -> AgentOrchestrator:
    return AgentOrchestrator(agent=agent, router=IntentRouter(model_provider=provider))


@pytest.fixture()
def memory_db_path(tmp_path: Path) -> Path:
    return tmp_path / "tom-memory.db"


def test_agent_remembers_and_recalls_memory_through_full_pipeline(
    memory_db_path: Path,
) -> None:
    manager = make_memory_manager(memory_db_path)
    provider = MockModelProvider(
        [
            tool_call(
                "memory.remember",
                {
                    "content": "User prefers Rust examples for systems topics.",
                    "type": "PREFERENCE",
                    "importance": "LOW",
                    "metadata": {"source": "integration"},
                },
                "remember-1",
            ),
            "Stored that preference.",
            tool_call(
                "memory.recall",
                {"query": "Rust examples", "types": ["PREFERENCE"], "limit": 5},
                "recall-1",
            ),
            "You prefer Rust examples for systems topics.",
        ]
    )
    agent = make_memory_agent(manager, provider)
    orchestrator = make_orchestrator(agent, provider)

    remembered = run_async(orchestrator.run("remember that I prefer Rust examples"))
    recalled = run_async(orchestrator.run("what programming examples do I prefer?"))

    assert remembered == "Stored that preference."
    assert recalled == "You prefer Rust examples for systems topics."
    assert provider.call_count == 4
    assert agent.memory_manager is manager

    stored = run_async(manager.preferences())
    assert len(stored) == 1
    assert stored[0].content == "User prefers Rust examples for systems topics."
    assert stored[0].memory_id in manager.qdrant._memory_vectors

    recall_observations = [msg for msg in agent.history if msg.name == "memory.recall"]
    assert recall_observations
    assert "Rust examples" in recall_observations[-1].content


def test_forget_memory_requires_confirmation_and_removes_sqlite_and_qdrant(
    memory_db_path: Path,
) -> None:
    manager = make_memory_manager(memory_db_path)
    # Store a memory to forget later with a known ID
    run_async(
        manager.store(
            MemoryRecord(
                memory_id="forget-me",
                type=MemoryType.TASK,
                content="Temporary reminder",
                importance=MemoryImportance.LOW,
            )
        )
    )
    assert run_async(manager.get("forget-me")) is not None
    assert "forget-me" in manager.qdrant._memory_vectors

    # Test denial: agent returns a tool call for memory.forget, but confirmation hook denies it
    deny_provider = MockModelProvider(
        [
            # First call: agent decides to call memory.forget
            tool_call(
                "memory.forget",
                {"memory_id": "forget-me"},
                "forget-call",
            ),
            # Second call: after tool execution returns observation, agent generates final response
            "Forget was denied.",
        ]
    )
    deny_agent = make_memory_agent(
        manager,
        deny_provider,
        confirmation_hook=AlwaysDenyConfirmationHook(),
    )
    deny_orchestrator = make_orchestrator(deny_agent, MockModelProvider(["Memory domain"]))
    denied = run_async(deny_orchestrator.run("forget that temporary reminder"))
    assert denied == "Forget was denied."
    # Memory should still exist
    assert run_async(manager.get("forget-me")) is not None
    assert "forget-me" in manager.qdrant._memory_vectors
    # Check that the agent observed the tool failure
    denied_observation = [m for m in deny_agent.history if m.name == "memory.forget"][-1]
    assert denied_observation.tool_metadata is not None
    assert denied_observation.tool_metadata.success is False

    # Test allowance: agent returns a tool call for memory.forget, and confirmation hook allows it
    allow_provider = MockModelProvider(
        [
            # First call: agent decides to call memory.forget
            tool_call(
                "memory.forget",
                {"memory_id": "forget-me"},
                "forget-call",
            ),
            # Second call: after tool execution returns observation, agent generates final response
            "Successfully forgot.",
        ]
    )
    allow_agent = make_memory_agent(
        manager,
        allow_provider,
        confirmation_hook=AlwaysAllowConfirmationHook(),
    )
    allow_orchestrator = make_orchestrator(allow_agent, MockModelProvider(["Memory domain"]))
    allowed = run_async(allow_orchestrator.run("forget that temporary reminder"))
    assert allowed == "Successfully forgot."
    # Memory should be gone
    assert run_async(manager.get("forget-me")) is None
    assert "forget-me" not in manager.qdrant._memory_vectors
    # Check that the agent observed the tool success
    allowed_observation = [m for m in allow_agent.history if m.name == "memory.forget"][-1]
    assert allowed_observation.tool_metadata is not None
    assert allowed_observation.tool_metadata.success is True


def test_persistence_across_new_memory_manager_instance(
    memory_db_path: Path,
) -> None:
    """Test that memories persist across new MemoryManager instances using the same SQLite database."""
    # First manager instance
    manager1 = make_memory_manager(memory_db_path)
    run_async(
        manager1.store(
            MemoryRecord(
                memory_id="persist-test",
                type=MemoryType.SEMANTIC,
                content="This should persist across manager instances",
                importance=MemoryImportance.LOW,  # Use LOW to avoid confirmation requirement
            )
        )
    )
    # Verify it'"'"'s stored in first manager
    assert run_async(manager1.get("persist-test")) is not None

    # Second manager instance with same database
    manager2 = make_memory_manager(memory_db_path)
    # Verify the memory persists
    persisted = run_async(manager2.get("persist-test"))
    assert persisted is not None
    assert persisted.content == "This should persist across manager instances"
    assert persisted.importance == MemoryImportance.LOW
    assert persisted.memory_id == "persist-test"

    # Verify SQLite authoritative behavior by checking Qdrant fallback
    # Add a vector only to Qdrant in manager2
    vector = run_async(manager2.embeddings.embed("ghost preference"))
    run_async(manager2.qdrant.upsert("ghost-id", vector))
    # Store a real memory in SQLite via manager2
    run_async(
        manager2.store(
            MemoryRecord(
                memory_id="real-id",
                type=MemoryType.SEMANTIC,
                content="Real SQLite memory about ghost preference.",
            )
        )
    )
    # Search should find only the real SQLite memory, not the Qdrant-only ghost
    results = run_async(manager2.search(MemoryQuery(text="ghost preference", limit=10)))
    result_ids = {result.record.memory_id for result in results}
    assert "real-id" in result_ids
    assert "ghost-id" not in result_ids


def test_secret_rejected_before_sqlite_or_qdrant_persistence(
    memory_db_path: Path,
) -> None:
    manager = make_memory_manager(memory_db_path)
    provider = MockModelProvider(
        [
            tool_call(
                "memory.remember",
                {
                    "content": "api_key = 'abcd1234efgh5678'",
                    "type": "SEMANTIC",
                    "importance": "LOW",
                },
                "secret-remember",
            ),
            "I cannot store secrets.",
        ]
    )
    agent = make_memory_agent(manager, provider)
    orchestrator = make_orchestrator(agent, provider)

    response = run_async(orchestrator.run("remember this api key"))
    recent = run_async(manager.recent(limit=10))

    assert response == "I cannot store secrets."
    assert recent == []
    assert manager.qdrant._memory_vectors == {}
    observation = [msg for msg in agent.history if msg.name == "memory.remember"][-1]
    assert observation.tool_metadata is not None
    assert observation.tool_metadata.success is False
    assert "sensitive credentials" in observation.content


def test_important_memory_requires_confirmation_before_persistence(
    memory_db_path: Path,
) -> None:
    denied_manager = make_memory_manager(memory_db_path)
    denied_provider = MockModelProvider(
        [
            tool_call(
                "memory.remember",
                {
                    "content": "User confirmed a durable preference.",
                    "type": "PREFERENCE",
                    "importance": "IMPORTANT",
                },
                "important-denied",
            ),
            "Important memory was not saved.",
        ]
    )
    denied_agent = make_memory_agent(
        denied_manager,
        denied_provider,
        confirmation_hook=AlwaysDenyConfirmationHook(),
    )

    denied_response = run_async(
        make_orchestrator(denied_agent, denied_provider).run("remember this important fact")
    )

    assert denied_response == "Important memory was not saved."
    assert run_async(denied_manager.recent(limit=10)) == []

    allowed_provider = MockModelProvider(
        [
            tool_call(
                "memory.remember",
                {
                    "content": "User confirmed a durable preference.",
                    "type": "PREFERENCE",
                    "importance": "IMPORTANT",
                },
                "important-allowed",
            ),
            "Important memory saved.",
        ]
    )
    allowed_agent = make_memory_agent(denied_manager, allowed_provider)

    allowed_response = run_async(
        make_orchestrator(allowed_agent, allowed_provider).run("remember this important fact")
    )
    saved = run_async(denied_manager.preferences())

    assert allowed_response == "Important memory saved."
    assert len(saved) == 1
    assert saved[0].importance == MemoryImportance.IMPORTANT
    assert saved[0].user_confirmed is True


def test_memory_router_classifies_memory_prompt_for_agent_loop(
    memory_db_path: Path,
) -> None:
    manager = make_memory_manager(memory_db_path)
    provider = MockModelProvider(["Memory request handled."])
    agent = make_memory_agent(manager, provider)
    router = IntentRouter(model_provider=provider)

    route = run_async(router.classify("please remember this preference"))
    response = run_async(AgentOrchestrator(agent=agent, router=router).run("hello"))

    assert route.domain == IntentDomain.MEMORY
    assert route.target_tool is None
    assert response == "Memory request handled."
