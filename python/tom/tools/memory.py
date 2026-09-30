"""TOM Deterministic Memory Tools.

Provides typed, deterministic memory tools integrating with MemoryManager:
- memory.remember (SAFE for LOW/USEFUL; ASK_USER for IMPORTANT/CRITICAL)
- memory.recall (SAFE)
- memory.forget (ASK_USER)
- memory.recent (SAFE)
- memory.preferences (SAFE)

Adheres to:
- PHASE5_IMPLEMENTATIONPLAN.md (Iteration 3)
- Decision 037: MemoryManager as Single Entry Point & SQLite Source of Truth
- tools/executor and security/permissions decoupled contracts
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tom.memory.manager import MemoryManager
from tom.schemas.memory import (
    MemoryImportance,
    MemoryQuery,
    MemoryRecord,
    MemoryType,
)
from tom.security.permissions import PermissionLevel
from tom.tools.registry import ToolDefinition, ToolRegistry, default_registry

# ---------------------------------------------------------------------------
# Pydantic Input and Output Schemas
# ---------------------------------------------------------------------------


class MemoryRememberInput(BaseModel):
    """Input schema for memory.remember tool."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, description="Text content to store in long-term memory")
    type: MemoryType = Field(default=MemoryType.SEMANTIC, description="Categorical type of memory")
    importance: MemoryImportance = Field(
        default=MemoryImportance.USEFUL, description="Importance level"
    )
    expires_at: datetime | None = Field(
        default=None, description="Optional expiration timestamp (UTC)"
    )
    source: str | None = Field(default="user_explicit", description="Origin of the memory")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Structured metadata")
    user_confirmed: bool = Field(default=False, description="Whether explicitly confirmed by user")


class MemoryRememberOutput(BaseModel):
    """Output schema for memory.remember tool."""

    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(description="Unique UUID of the stored memory record")
    status: str = Field(default="stored", description="Status of the store operation")


class MemoryRecallInput(BaseModel):
    """Input schema for memory.recall tool."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, description="Search query string")
    types: list[MemoryType] | None = Field(
        default=None, description="Optional memory types to filter by"
    )
    importance_min: MemoryImportance | None = Field(
        default=None, description="Minimum importance filter"
    )
    limit: int = Field(default=10, ge=1, le=100, description="Maximum number of memories to return")
    include_expired: bool = Field(default=False, description="Whether to include expired memories")


class MemoryRecallOutput(BaseModel):
    """Output schema for memory.recall tool."""

    model_config = ConfigDict(extra="forbid")

    results: list[dict[str, Any]] = Field(
        description="List of retrieved memory results with scores"
    )
    total: int = Field(description="Number of results returned")


class MemoryForgetInput(BaseModel):
    """Input schema for memory.forget tool."""

    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(min_length=1, description="Unique UUID of the memory to delete")


class MemoryForgetOutput(BaseModel):
    """Output schema for memory.forget tool."""

    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(description="Unique UUID of the target memory")
    deleted: bool = Field(description="Whether the memory was successfully deleted")


class MemoryRecentInput(BaseModel):
    """Input schema for memory.recent tool."""

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(
        default=10, ge=1, le=100, description="Maximum number of recent memories to return"
    )


class MemoryRecentOutput(BaseModel):
    """Output schema for memory.recent tool."""

    model_config = ConfigDict(extra="forbid")

    memories: list[dict[str, Any]] = Field(description="List of recent memory records")
    total: int = Field(description="Number of memories returned")


class MemoryPreferencesInput(BaseModel):
    """Input schema for memory.preferences tool."""

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(
        default=50, ge=1, le=200, description="Maximum number of preferences to return"
    )


class MemoryPreferencesOutput(BaseModel):
    """Output schema for memory.preferences tool."""

    model_config = ConfigDict(extra="forbid")

    preferences: list[dict[str, Any]] = Field(description="List of user preference records")
    total: int = Field(description="Number of preferences returned")


# ---------------------------------------------------------------------------
# Tool Factory & Registration
# ---------------------------------------------------------------------------


def create_memory_tools(
    memory_manager: MemoryManager | None = None,
) -> list[ToolDefinition]:
    """Create the suite of deterministic memory tool definitions.

    Args:
        memory_manager: Injected MemoryManager instance. If None, lazily constructed.

    Returns:
        List of 5 ToolDefinition instances.
    """
    _mgr: MemoryManager | None = memory_manager

    def _get_manager() -> MemoryManager:
        nonlocal _mgr
        if _mgr is None:
            _mgr = MemoryManager()
        return _mgr

    async def remember(params: MemoryRememberInput) -> MemoryRememberOutput:
        mgr = _get_manager()
        # If executed through tool pipeline, confirmation was already resolved by ToolExecutor
        user_confirmed = params.user_confirmed or (
            params.importance in (MemoryImportance.IMPORTANT, MemoryImportance.CRITICAL)
        )
        record = MemoryRecord(
            content=params.content,
            type=params.type,
            importance=params.importance,
            expires_at=params.expires_at,
            source=params.source,
            metadata=params.metadata,
            user_confirmed=user_confirmed,
        )
        saved_id = await mgr.store(record)
        return MemoryRememberOutput(memory_id=saved_id, status="stored")

    async def recall(params: MemoryRecallInput) -> MemoryRecallOutput:
        mgr = _get_manager()
        query = MemoryQuery(
            text=params.query,
            types=params.types,
            importance_min=params.importance_min,
            limit=params.limit,
            include_expired=params.include_expired,
        )
        search_results = await mgr.search(query)
        results_data = [
            {
                "memory_id": r.record.memory_id,
                "content": r.record.content,
                "type": r.record.type.value,
                "importance": r.record.importance.value,
                "score": r.score,
                "created_at": r.record.created_at.isoformat(),
                "expires_at": r.record.expires_at.isoformat() if r.record.expires_at else None,
                "metadata": r.record.metadata,
            }
            for r in search_results
        ]
        return MemoryRecallOutput(results=results_data, total=len(results_data))

    async def forget(params: MemoryForgetInput) -> MemoryForgetOutput:
        mgr = _get_manager()
        deleted = await mgr.forget(params.memory_id)
        return MemoryForgetOutput(memory_id=params.memory_id, deleted=deleted)

    async def recent(params: MemoryRecentInput | None = None) -> MemoryRecentOutput:
        mgr = _get_manager()
        limit = params.limit if params is not None else 10
        records = await mgr.recent(limit=limit)
        memories_data = [
            {
                "memory_id": r.memory_id,
                "content": r.content,
                "type": r.type.value,
                "importance": r.importance.value,
                "created_at": r.created_at.isoformat(),
                "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                "metadata": r.metadata,
            }
            for r in records
        ]
        return MemoryRecentOutput(memories=memories_data, total=len(memories_data))

    async def preferences(params: MemoryPreferencesInput | None = None) -> MemoryPreferencesOutput:
        mgr = _get_manager()
        limit = params.limit if params is not None else 50
        records = await mgr.preferences()
        if limit and len(records) > limit:
            records = records[:limit]
        prefs_data = [
            {
                "memory_id": r.memory_id,
                "content": r.content,
                "type": r.type.value,
                "importance": r.importance.value,
                "created_at": r.created_at.isoformat(),
                "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                "metadata": r.metadata,
            }
            for r in records
        ]
        return MemoryPreferencesOutput(preferences=prefs_data, total=len(prefs_data))

    return [
        ToolDefinition(
            name="memory.remember",
            description="Store or record a new fact, preference, or event in TOM long-term memory.",
            category="memory",
            input_schema=MemoryRememberInput,
            output_schema=MemoryRememberOutput,
            permission_level=PermissionLevel.SAFE,
            handler=remember,
        ),
        ToolDefinition(
            name="memory.recall",
            description="Search and retrieve relevant long-term memories using semantic and keyword matching.",
            category="memory",
            input_schema=MemoryRecallInput,
            output_schema=MemoryRecallOutput,
            permission_level=PermissionLevel.SAFE,
            handler=recall,
        ),
        ToolDefinition(
            name="memory.forget",
            description="Permanently delete a specific memory record by its unique memory ID.",
            category="memory",
            input_schema=MemoryForgetInput,
            output_schema=MemoryForgetOutput,
            permission_level=PermissionLevel.ASK_USER,
            handler=forget,
        ),
        ToolDefinition(
            name="memory.recent",
            description="Retrieve the most recently created or updated long-term memories.",
            category="memory",
            input_schema=MemoryRecentInput,
            output_schema=MemoryRecentOutput,
            permission_level=PermissionLevel.SAFE,
            handler=recent,
        ),
        ToolDefinition(
            name="memory.preferences",
            description="Retrieve active user preferences stored in long-term memory.",
            category="memory",
            input_schema=MemoryPreferencesInput,
            output_schema=MemoryPreferencesOutput,
            permission_level=PermissionLevel.SAFE,
            handler=preferences,
        ),
    ]


def register_memory_tools(
    registry: ToolRegistry | None = None,
    memory_manager: MemoryManager | None = None,
    replace: bool = True,
) -> list[ToolDefinition]:
    """Register all deterministic memory tools into a ToolRegistry.

    Args:
        registry: Target ToolRegistry. Defaults to global default_registry.
        memory_manager: Optional injected MemoryManager.
        replace: Whether to overwrite existing registrations.

    Returns:
        List of registered ToolDefinition objects.
    """
    target_registry = registry if registry is not None else default_registry
    tools = create_memory_tools(memory_manager=memory_manager)
    for t in tools:
        target_registry.register(t, replace=replace)
    return tools


__all__ = [
    "MemoryForgetInput",
    "MemoryForgetOutput",
    "MemoryPreferencesInput",
    "MemoryPreferencesOutput",
    "MemoryRecallInput",
    "MemoryRecallOutput",
    "MemoryRecentInput",
    "MemoryRecentOutput",
    "MemoryRememberInput",
    "MemoryRememberOutput",
    "create_memory_tools",
    "register_memory_tools",
]
