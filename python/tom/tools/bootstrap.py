"""TOM Tool Bootstrap — Default Tool Registration.

This module provides the canonical tool registration entry point for the TOM
Python pipeline. It wires all deterministic tool sets into the shared
``default_registry`` so that ``ToolExecutor`` can discover and execute them
without requiring additional setup by callers.

Architecture note (Decision 030):
    TOM's agent/orchestration layer is not yet implemented (Phase 4+).  The
    ``LifecycleManager`` owns IPC and EngineClient bootstrap; a future agent
    will own the request loop.  This module provides the **smallest clean
    integration seam** between the tool implementation layer (Iterations 1–5)
    and any future consumer: call ``setup_default_tools()`` once after
    lifecycle start to make all tools available through the normal registry.

    The function is intentionally free of LLM, model-router, or agent
    dependencies.  It registers tools only into the explicitly supplied or
    global ``default_registry`` — never into an unrelated custom registry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tom.tools.files import PathGuard, register_file_tools
from tom.tools.registry import ToolDefinition, ToolRegistry, default_registry
from tom.tools.system import register_system_tools

if TYPE_CHECKING:
    from tom.core.engine import EngineClient
    from tom.schemas.config import ToolsConfig


def setup_default_tools(
    registry: ToolRegistry | None = None,
    engine_client: EngineClient | None = None,
    path_guard: PathGuard | None = None,
    config: ToolsConfig | None = None,
    replace: bool = True,
) -> tuple[list[ToolDefinition], list[ToolDefinition]]:
    """Register all built-in deterministic tools into a ToolRegistry.

    This is the canonical bootstrap entry point for Phase 3 tools.  It must
    be called once after lifecycle startup (or in test setup) to populate the
    registry before any ``ToolExecutor`` dispatches occur.

    Args:
        registry: Target ``ToolRegistry``.  Defaults to ``default_registry``
                  when ``None``.  An explicitly-supplied **empty** registry is
                  always respected (checked via ``is not None``, not truthiness).
        engine_client: Optional live ``EngineClient`` for system telemetry
                       delegation.  When ``None`` the system tools fall back to
                       ``psutil`` / standard library.
        path_guard: Optional ``PathGuard`` instance for file tools.  When
                    ``None`` a guard is constructed from *config* defaults.
        config: Optional ``ToolsConfig`` carrying sandbox directories, read
                limits, and timeout settings.  When ``None`` defaults are used.
        replace: Whether to silently overwrite already-registered tool names.
                 Defaults to ``True`` to allow safe re-registration on restart.

    Returns:
        A 2-tuple ``(system_tools, file_tools)`` — each being the list of
        ``ToolDefinition`` objects that were registered.

    Example::

        from tom.tools.bootstrap import setup_default_tools
        system_tools, file_tools = setup_default_tools()
        # All 13 built-in tools are now available via default_registry / ToolExecutor.
    """
    target_registry: ToolRegistry = registry if registry is not None else default_registry

    system_defs = register_system_tools(
        registry=target_registry,
        client=engine_client,
        replace=replace,
    )

    file_defs = register_file_tools(
        registry=target_registry,
        path_guard=path_guard,
        config=config,
        replace=replace,
    )

    return system_defs, file_defs
