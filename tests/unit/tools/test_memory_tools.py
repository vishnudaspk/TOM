"""Unit tests for TOM deterministic memory tools.

Adheres to:
- PHASE5_IMPLEMENTATIONPLAN.md (Iteration 3)
- Decision 039: Memory Security Policies & Non-Bypassable Memory Tools
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from tom.agents.dependencies import AgentDependencies
from tom.schemas.memory import MemoryImportance, MemoryRecord, MemorySearchResult, MemoryType
from tom.security.confirmation import (
    AlwaysAllowConfirmationHook,
    AlwaysDenyConfirmationHook,
)
from tom.security.permissions import (
    PermissionDeniedError,
    PermissionEngine,
    PermissionLevel,
)
from tom.tools.bootstrap import setup_default_tools
from tom.tools.executor import ToolExecutor
from tom.tools.memory import create_memory_tools, register_memory_tools
from tom.tools.registry import ToolRegistry


def run_async(coro: Any) -> Any:
    return asyncio.run(coro)


class FakeMemoryManager:
    def __init__(self) -> None:
        self.stored_records: list[MemoryRecord] = []
        self.forgotten_ids: list[str] = []
        self.records = [
            MemoryRecord(
                memory_id="pref-1",
                type=MemoryType.PREFERENCE,
                content="User prefers Python examples.",
                importance=MemoryImportance.USEFUL,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            MemoryRecord(
                memory_id="task-1",
                type=MemoryType.TASK,
                content="Finish Phase 5 Iteration 3 tests.",
                importance=MemoryImportance.LOW,
                created_at=datetime(2026, 1, 2, tzinfo=UTC),
            ),
        ]

    async def store(self, record: MemoryRecord) -> str:
        self.stored_records.append(record)
        return "stored-id"

    async def search(self, query: Any) -> list[MemorySearchResult]:
        matches = [
            record
            for record in self.records
            if query.text.lower() in record.content.lower()
            and (query.types is None or record.type in query.types)
        ]
        return [MemorySearchResult(record=record, score=0.75) for record in matches[: query.limit]]

    async def forget(self, memory_id: str) -> bool:
        self.forgotten_ids.append(memory_id)
        return memory_id == "pref-1"

    async def recent(self, limit: int = 10) -> list[MemoryRecord]:
        return self.records[:limit]

    async def preferences(self) -> list[MemoryRecord]:
        return [record for record in self.records if record.type == MemoryType.PREFERENCE]


EXPECTED_MEMORY_TOOLS = {
    "memory.remember",
    "memory.recall",
    "memory.forget",
    "memory.recent",
    "memory.preferences",
}


def test_create_memory_tools_returns_all_definitions() -> None:
    tools = create_memory_tools(memory_manager=FakeMemoryManager())  # type: ignore[arg-type]

    assert {tool.name for tool in tools} == EXPECTED_MEMORY_TOOLS
    assert all(tool.category == "memory" for tool in tools)


def test_register_memory_tools_discovers_all_tools() -> None:
    registry = ToolRegistry()
    defs = register_memory_tools(
        registry=registry,
        memory_manager=FakeMemoryManager(),  # type: ignore[arg-type]
    )

    assert len(defs) == 5
    assert {tool.name for tool in registry.list_tools(category="memory")} == EXPECTED_MEMORY_TOOLS


def test_memory_tool_permission_contract() -> None:
    registry = ToolRegistry()
    setup_default_tools(
        registry=registry,
        memory_manager=FakeMemoryManager(),  # type: ignore[arg-type]
    )
    engine = PermissionEngine()

    assert registry.get("memory.recall").permission_level == PermissionLevel.SAFE
    assert registry.get("memory.forget").permission_level == PermissionLevel.ASK_USER

    low_decision = engine.evaluate(
        registry.get("memory.remember"),
        params={"content": "User prefers concise answers.", "importance": "LOW"},
    )
    important_decision = engine.evaluate(
        registry.get("memory.remember"),
        params={"content": "Remember this important preference.", "importance": "IMPORTANT"},
    )
    secret_decision = engine.evaluate(
        registry.get("memory.remember"),
        params={"content": "api_key = 'abcd1234efgh5678'", "importance": "LOW"},
    )

    assert low_decision.permission_level == PermissionLevel.SAFE
    assert low_decision.requires_confirmation is False
    assert important_decision.permission_level == PermissionLevel.ASK_USER
    assert important_decision.requires_confirmation is True
    assert secret_decision.allowed is False
    assert secret_decision.permission_level == PermissionLevel.BLOCK


def test_memory_remember_executes_through_tool_executor() -> None:
    manager = FakeMemoryManager()
    registry = ToolRegistry()
    register_memory_tools(registry=registry, memory_manager=manager)  # type: ignore[arg-type]
    executor = ToolExecutor(registry=registry, confirmation_hook=AlwaysAllowConfirmationHook())

    result = run_async(
        executor.execute(
            "memory.remember",
            {
                "content": "User prefers Python examples.",
                "type": "PREFERENCE",
                "importance": "LOW",
                "metadata": {"topic": "style"},
            },
        )
    )

    assert result.success is True
    assert result.data.memory_id == "stored-id"
    assert manager.stored_records[0].type == MemoryType.PREFERENCE
    assert manager.stored_records[0].metadata == {"topic": "style"}


def test_memory_remember_important_requires_confirmation_before_execution() -> None:
    manager = FakeMemoryManager()
    registry = ToolRegistry()
    register_memory_tools(registry=registry, memory_manager=manager)  # type: ignore[arg-type]
    executor = ToolExecutor(registry=registry, confirmation_hook=AlwaysDenyConfirmationHook())

    with pytest.raises(PermissionDeniedError):
        run_async(
            executor.execute(
                "memory.remember",
                {
                    "content": "This important memory needs confirmation.",
                    "importance": "IMPORTANT",
                },
            )
        )

    assert manager.stored_records == []


def test_memory_remember_confirmed_important_marks_record_confirmed() -> None:
    manager = FakeMemoryManager()
    registry = ToolRegistry()
    register_memory_tools(registry=registry, memory_manager=manager)  # type: ignore[arg-type]
    executor = ToolExecutor(registry=registry, confirmation_hook=AlwaysAllowConfirmationHook())

    result = run_async(
        executor.execute(
            "memory.remember",
            {
                "content": "This important memory was confirmed.",
                "importance": "IMPORTANT",
            },
        )
    )

    assert result.success is True
    assert manager.stored_records[0].user_confirmed is True


def test_memory_recall_returns_validated_tool_result() -> None:
    manager = FakeMemoryManager()
    registry = ToolRegistry()
    register_memory_tools(registry=registry, memory_manager=manager)  # type: ignore[arg-type]
    executor = ToolExecutor(registry=registry)

    result = run_async(
        executor.execute(
            "memory.recall",
            {"query": "python", "types": ["PREFERENCE"], "limit": 5},
        )
    )

    assert result.success is True
    assert result.data.total == 1
    assert result.data.results[0]["memory_id"] == "pref-1"
    assert result.data.results[0]["score"] == 0.75


def test_memory_forget_requires_confirmation() -> None:
    manager = FakeMemoryManager()
    registry = ToolRegistry()
    register_memory_tools(registry=registry, memory_manager=manager)  # type: ignore[arg-type]
    denied_executor = ToolExecutor(
        registry=registry, confirmation_hook=AlwaysDenyConfirmationHook()
    )

    with pytest.raises(PermissionDeniedError):
        run_async(denied_executor.execute("memory.forget", {"memory_id": "pref-1"}))
    assert manager.forgotten_ids == []

    allowed_executor = ToolExecutor(
        registry=registry, confirmation_hook=AlwaysAllowConfirmationHook()
    )
    result = run_async(allowed_executor.execute("memory.forget", {"memory_id": "pref-1"}))
    assert result.success is True
    assert result.data.deleted is True
    assert manager.forgotten_ids == ["pref-1"]


def test_memory_recent_and_preferences_execute_through_tool_executor() -> None:
    manager = FakeMemoryManager()
    registry = ToolRegistry()
    register_memory_tools(registry=registry, memory_manager=manager)  # type: ignore[arg-type]
    executor = ToolExecutor(registry=registry)

    recent = run_async(executor.execute("memory.recent", {"limit": 2}))
    prefs = run_async(executor.execute("memory.preferences", {"limit": 10}))

    assert recent.success is True
    assert recent.data.total == 2
    assert [item["memory_id"] for item in recent.data.memories] == ["pref-1", "task-1"]
    assert prefs.success is True
    assert prefs.data.total == 1
    assert prefs.data.preferences[0]["memory_id"] == "pref-1"


def test_agent_dependencies_carries_memory_manager() -> None:
    manager = FakeMemoryManager()
    deps = AgentDependencies(memory_manager=manager)

    assert deps.memory_manager is manager
