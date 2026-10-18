"""TOM Tool Bootstrap — Default Tool Registration.

This module provides the canonical tool registration entry point for the TOM
Python pipeline. It wires all deterministic tool sets (system, file, memory,
voice, vision, input, web, and personal) into the shared ``default_registry`` so
that ``ToolExecutor`` can discover and execute them without requiring additional
setup by callers.

Architecture note (Decision 030, Decision 037, Decision 045, Phase 7 Iteration 4, Phase 10 Iterations 1-2, 4-5):
    Wires deterministic tool sets into registry. Call ``setup_default_tools()``
    once after lifecycle start or test setup to make all 34 tools available.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tom.tools.files import PathGuard, register_file_tools
from tom.tools.input import register_input_tools
from tom.tools.memory import register_memory_tools
from tom.tools.personal import create_personal_tools
from tom.tools.registry import ToolDefinition, ToolRegistry, default_registry
from tom.tools.system import register_system_tools
from tom.tools.vision import register_vision_tools
from tom.tools.voice import register_voice_tools
from tom.tools.web import create_web_tools

if TYPE_CHECKING:
    from tom.core.engine import EngineClient
    from tom.memory.manager import MemoryManager
    from tom.personal.reminders import ReminderService
    from tom.personal.store import PersonalStore
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
    personal_store: PersonalStore | None = None,
    reminder_service: ReminderService | None = None,
    dry_run_input: bool = False,
    replace: bool = True,
) -> tuple[list[ToolDefinition], ...]:
    """Register all built-in deterministic tools into a ToolRegistry.

    Registers 34 built-in tools across 8 categories:
    - 7 system inspection tools (SAFE)
    - 6 file manipulation tools (SAFE & ASK_USER)
    - 5 memory storage tools (SAFE & ASK_USER)
    - 2 voice interaction tools (SAFE)
    - 5 vision perception tools (SAFE) — includes vision.analyze (Phase 10 Iteration 5)
    - 4 OS input automation tools (SAFE & ASK_USER)
    - 2 web access tools (SAFE) — Phase 10 Iterations 1-2
    - 3 personal productivity tools (SAFE reads; mutations ASK_USER) — Phase 10 Iteration 4

    Args:
        registry: Target ``ToolRegistry``. Defaults to ``default_registry`` when ``None``.
        engine_client: Optional live ``EngineClient`` for system telemetry and OS input delegation.
        path_guard: Optional ``PathGuard`` instance for file tools.
        memory_manager: Optional injected ``MemoryManager`` for memory tools.
        config: Optional ``ToolsConfig`` for file tools sandbox configuration.
        voice_manager: Optional injected ``VoiceInteractionManager`` for voice tools.
        vision_manager: Optional injected ``VisionManager`` for vision tools.
        personal_store: Optional injected ``PersonalStore`` for personal tools; when
            None the personal tools lazily construct a default store on first use.
        reminder_service: Optional injected ``ReminderService`` bridging ProactiveScheduler.
        dry_run_input: Whether input tools run in simulated dry-run mode (default False).
        replace: Whether to overwrite already-registered tool names (default True).

    Returns:
        A tuple of registered ToolDefinition lists:
        ``(system_defs, file_defs, memory_defs, voice_defs, vision_defs, input_defs)``.
        Supports both 4-tuple unpacking (backward-compatible) and 6-tuple unpacking.
        Personal and web tools are registered into the registry directly (matching
        the existing web-tool pattern) and are verified through the registry listing.
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

    create_web_tools(
        registry=target_registry,
        config=config,
        replace=replace,
    )

    create_personal_tools(
        registry=target_registry,
        store=personal_store,
        reminder_service=reminder_service,
        replace=replace,
    )

    return ToolBootstrapResult(
        (system_defs, file_defs, memory_defs, voice_defs, vision_defs, input_defs)
    )
