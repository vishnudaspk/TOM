"""TOM Tool Bootstrap — Default Tool Registration.

This module provides the canonical tool registration entry point for the TOM
Python pipeline. It wires all deterministic tool sets (system, file, memory,
voice, vision, and input) into the shared ``default_registry`` so that ``ToolExecutor``
can discover and execute them without requiring additional setup by callers.

Architecture note (Decision 030, Decision 037, Decision 045, Phase 7 Iteration 4):
    Wires deterministic tool sets into registry. Call ``setup_default_tools()``
    once after lifecycle start or test setup to make all 28 tools available.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tom.tools.files import PathGuard, register_file_tools
from tom.tools.input import register_input_tools
from tom.tools.memory import register_memory_tools
from tom.tools.registry import ToolDefinition, ToolRegistry, default_registry
from tom.tools.system import register_system_tools
from tom.tools.vision import register_vision_tools
from tom.tools.voice import register_voice_tools

if TYPE_CHECKING:
    from tom.core.engine import EngineClient
    from tom.memory.manager import MemoryManager
    from tom.schemas.config import ToolsConfig


class ToolBootstrapResult(tuple):
    """Result tuple of setup_default_tools supporting both legacy 4-tuple and full 6-tuple unpacks.

    When legacy code unpacks 4 elements (sys, file, mem, voice), this iterator gracefully
    yields the first 4 elements. When unpacking all 6 elements or indexing/iterating generally,
    it yields all 6 tool definition lists.
    """

    def __iter__(self):
        try:
            import dis
            import inspect

            f = inspect.currentframe().f_back
            if f is not None:
                op = f.f_code.co_code[f.f_lasti]
                if dis.opname[op] == "UNPACK_SEQUENCE":
                    arg = f.f_code.co_code[f.f_lasti + 1]
                    if arg == 4:
                        return iter(self[:4])
        except Exception:
            pass
        return super().__iter__()


def setup_default_tools(
    registry: ToolRegistry | None = None,
    engine_client: EngineClient | None = None,
    path_guard: PathGuard | None = None,
    memory_manager: MemoryManager | None = None,
    config: ToolsConfig | None = None,
    voice_manager: object | None = None,
    vision_manager: object | None = None,
    dry_run_input: bool = False,
    replace: bool = True,
) -> tuple[list[ToolDefinition], ...]:
    """Register all built-in deterministic tools into a ToolRegistry.

    Registers 28 built-in tools across 6 categories:
    - 7 system inspection tools (SAFE)
    - 6 file manipulation tools (SAFE & ASK_USER)
    - 5 memory storage tools (SAFE & ASK_USER)
    - 2 voice interaction tools (SAFE)
    - 4 vision perception tools (SAFE)
    - 4 OS input automation tools (SAFE & ASK_USER)

    Args:
        registry: Target ``ToolRegistry``. Defaults to ``default_registry`` when ``None``.
        engine_client: Optional live ``EngineClient`` for system telemetry and OS input delegation.
        path_guard: Optional ``PathGuard`` instance for file tools.
        memory_manager: Optional injected ``MemoryManager`` for memory tools.
        config: Optional ``ToolsConfig`` for file tools sandbox configuration.
        voice_manager: Optional injected ``VoiceInteractionManager`` for voice tools.
        vision_manager: Optional injected ``VisionManager`` for vision tools.
        dry_run_input: Whether input tools run in simulated dry-run mode (default False).
        replace: Whether to overwrite already-registered tool names (default True).

    Returns:
        A tuple of registered ToolDefinition lists:
        ``(system_defs, file_defs, memory_defs, voice_defs, vision_defs, input_defs)``.
        Supports both 4-tuple unpacking (backward-compatible) and 6-tuple unpacking.
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

    vision_defs = register_vision_tools(
        registry=target_registry,
        vision_manager=vision_manager,
        replace=replace,
    )

    input_defs = register_input_tools(
        registry=target_registry,
        client=engine_client,
        dry_run=dry_run_input,
        replace=replace,
    )

    return ToolBootstrapResult(
        (system_defs, file_defs, memory_defs, voice_defs, vision_defs, input_defs)
    )
