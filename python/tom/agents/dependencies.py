"""TOM Agent Dependencies ? Dependency Injection Container.

Adheres to:
- PHASE4_IMPLEMENTATIONPLAN.md ?5 (Iterations 2 & 3)
- PHASE5_IMPLEMENTATIONPLAN.md (Iteration 3)
- Decision 032: Centralized Tool Invocation Safety (Strict ToolExecutor Routing)
- Decision 033: Decoupled Model Provider Protocol & LM Studio (Bionic) Compatibility
- Decision 037: MemoryManager as Single Entry Point & SQLite Source of Truth
- skills/python/tool-system, skills/security/permission-model
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from tom.models.base import LLMProvider
from tom.tools.executor import ToolExecutor
from tom.tools.files import PathGuard
from tom.tools.registry import ToolRegistry, default_registry

if TYPE_CHECKING:
    pass


class AgentDependencies(BaseModel):
    """Injectable dependency container for TOM Agents.

    Carries the ToolRegistry, ToolExecutor, PathGuard, optional LifecycleManager,
    optional LLMProvider, and optional MemoryManager. All dependencies are optional ?
    constructing an Agent without dependencies is valid (no network access required).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    registry: ToolRegistry = Field(default_factory=lambda: default_registry)
    executor: ToolExecutor | None = Field(default=None)
    path_guard: PathGuard | None = Field(default=None)
    # Use object | None to avoid Pydantic forward-reference issues with LifecycleManager.
    lifecycle_manager: object | None = Field(default=None)
    # LLMProvider abstract interface (Decision 033).
    model_provider: LLMProvider | None = Field(default=None)
    # Injectable MemoryManager for long-term memory operations (Iteration 3).
    memory_manager: object | None = Field(default=None)
    # Injectable voice coordinator or pipeline manager (Phase 6 Iteration 4).
    voice_manager: object | None = Field(default=None)

    def get_executor(self) -> ToolExecutor:
        """Return or lazily construct a ToolExecutor bound to the registry."""
        if self.executor is None:
            self.executor = ToolExecutor(registry=self.registry)
        return self.executor

    def get_voice_manager(self) -> object | None:
        """Return injected voice interaction or pipeline manager if configured."""
        return self.voice_manager
