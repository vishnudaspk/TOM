"""TOM Tool Bootstrap — Default Tool Registration.

This module provides the canonical tool registration entry point for the TOM
Python pipeline. It wires all deterministic tool sets (system, file, memory,
and voice) into the shared ``default_registry`` so that ``ToolExecutor`` can
discover and execute them without requiring additional setup by callers.

Architecture note (Decision 030, Decision 037, Decision 045):
    Wires deterministic tool sets into registry. Call ``setup_default_tools()``
    once after lifecycle start or test setup to make all tools available."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tom.tools.files import PathGuard, register_file_tools
from tom.tools.memory import register_memory_tools
from tom.tools.registry import ToolDefinition, ToolRegistry, default_registry
from tom.tools.system import register_system_tools
from tom.tools.voice import register_voice_tools

if TYPE_CHECKING:
    from tom.core.engine import EngineClient
    from tom.memory.manager import MemoryManager
    from tom.schemas.config import ToolsConfig


def setup_default_tools(
    registry: ToolRegistry | None = None,
    engine_client: EngineClient | None = None,
    path_guard: PathGuard | None = None,
    memory_manager: MemoryManager | None = None,
    config: ToolsConfig | None = None,
    voice_manager: object | None = None,
    replace: bool = True,
) -> tuple[list[ToolDefinition], list[ToolDefinition], list[ToolDefinition], list[ToolDefinition]]:
    """Register all built-in deterministic tools (system, files, memory, voice) into a ToolRegistry.

    Args:
        registry: Target ``ToolRegistry``. Defaults to ``default_registry`` when ``None``.
        engine_client: Optional live ``EngineClient`` for system telemetry delegation.
        path_guard: Optional ``PathGuard`` instance for file tools.
        memory_manager: Optional injected ``MemoryManager`` for memory tools.
        config: Optional ``ToolsConfig`` for file tools sandbox configuration.
        voice_manager: Optional injected ``VoiceInteractionManager`` for voice tools.
        replace: Whether to overwrite already-registered tool names (default True).

    Returns:
        A 4-tuple ``(system_tools, file_tools, memory_tools, voice_tools)`` of registered ToolDefinitions.
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

    memory_defs = register_memory_tools(
        registry=target_registry,
        memory_manager=memory_manager,
        replace=replace,
    )

    voice_defs = register_voice_tools(
        registry=target_registry,
        voice_manager=voice_manager,
        replace=replace,
    )

    return system_defs, file_defs, memory_defs, voice_defs
