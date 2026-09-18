"""TOM Agent Dependencies — Dependency Injection Container.

Adheres to:
- PHASE4_IMPLEMENTATIONPLAN.md §5 (Iterations 2 & 3)
- Decision 032: Centralized Tool Invocation Safety (Strict ToolExecutor Routing)
- Decision 033: Decoupled Model Provider Protocol & LM Studio (Bionic) Compatibility
- skills/python/tool-system, skills/security/permission-model
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from tom.models.base import LLMProvider
from tom.tools.executor import ToolExecutor
from tom.tools.files import PathGuard
from tom.tools.registry import ToolRegistry, default_registry


class AgentDependencies(BaseModel):
    """Injectable dependency container for TOM Agents.

    Carries the ToolRegistry, ToolExecutor, PathGuard, optional LifecycleManager,
    and optional LLMProvider. All dependencies are optional — constructing an Agent
    without a ModelProvider or ToolExecutor is valid (no network access required).

    Decision 032: Tool operations always flow through ToolExecutor (never bypassed).
    Decision 033: Agent depends on LLMProvider abstract interface — never on a
                  concrete provider like LMStudioProvider directly.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    registry: ToolRegistry = Field(default_factory=lambda: default_registry)
    executor: ToolExecutor | None = Field(default=None)
    path_guard: PathGuard | None = Field(default=None)
    # Use object | None to avoid Pydantic forward-reference issues with LifecycleManager.
    lifecycle_manager: object | None = Field(default=None)
    # LLMProvider abstract interface (Decision 033).
    # May be MockModelProvider in tests or HttpModelProvider / LMStudioProvider in production.
    model_provider: LLMProvider | None = Field(default=None)

    def get_executor(self) -> ToolExecutor:
        """Return or lazily construct a ToolExecutor bound to the registry."""
        if self.executor is None:
            self.executor = ToolExecutor(registry=self.registry)
        return self.executor
