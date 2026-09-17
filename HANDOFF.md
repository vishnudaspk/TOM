# TOM Agent Handoff

## Current Position
- **Current Phase**: Phase 3 — Deterministic Tools & Permission Engine (**CLOSED & COMPLETE**)
- **Next Phase**: **Phase 4 — Agent Framework & Local Model Routing**
- **Completed Milestones**:
  - **Phase 0**: Project foundation, schemas, YAML config loader, structured logging.
  - **Phase 1**: Rust engine (`tom-engine`), named pipe IPC server, system telemetry, audio/hotkey foundations (79 tests).
  - **Phase 2**: Python core, named pipe IPC client, typed `EngineClient`, `LifecycleManager` (168 tests, P95 0.359 ms).
  - **Phase 3 (Tools & Security — 6/6 Iterations Complete)**:
    - Iteration 1: `ToolDefinition`, `ToolResult`, `PermissionLevel`, `ToolRegistry`, `@tool` decorator (39 tests).
    - Iteration 2: `PermissionEngine`, `PermissionDecision`, `ConfirmationHook` implementations (33 tests).
    - Iteration 3: `ToolExecutor` (lookup, permissions, confirmation, Pydantic validation, timeouts, cancellation, monotonic timing) (22 tests).
    - Iteration 4+5 (Combined): Deterministic System Tools (`system.py`, 7 tools, 16 tests) + Sandboxed File Tools with `PathGuard` (`files.py`, 6 tools, 17 tests).
    - Iteration 6: Integration Pipeline & Exit Gate (`bootstrap.py`, 32 integration tests in `test_tool_pipeline.py`).
- **Environment**: Dedicated `.venv` at `C:\Users\vishnuu\Projects\TOM\.venv` (Python 3.11.9, pytest 9.1.1, ruff 0.16.7).
- **Final Phase 3 Test Baseline**:
  - Python Unit: **280 passed** (`pytest tests/unit/ -v`)
  - Python Integration: **47 passed** (`pytest tests/integration/ -v`)
  - Total Python: **327 passed**
  - Rust: **79 passed** (`cargo test` in `rust/tom-engine`)
  - Total Verified Tests: **406 passed** (0 failures, 0 regressions)
  - Quality Gates: Ruff clean (0 violations, 0 diffs), Cargo clippy/fmt clean

---

## Architectural Foundation for Phase 4
Phase 4 (Agent Framework & Local Model Routing) must build upon the completed Phase 3 components. **Do NOT rebuild, redesign, or duplicate any of the following**:

1. **Tool Registration Seam**:
   - Call `tom.tools.bootstrap.setup_default_tools(registry=...)` to populate any `ToolRegistry` (or the global `default_registry`) with all 13 built-in tools.
   - It is idempotent, supports custom sandboxes, and handles dependency injection cleanly.
2. **Deterministic Tool Pipeline**:
   - `ToolRequest` → `ToolRegistry` → `PermissionEngine` → `ConfirmationHook` → `ToolExecutor` → Handler → `ToolResult`.
   - Tool execution, pre-execution permission evaluation, argument validation, timeout management, and cancellation safety are fully implemented and tested.
3. **13 Built-in Tools**:
   - `system.*` (7): `cpu_info`, `memory_info`, `gpu_info`, `battery_info`, `disk_info`, `list_processes`, `get_snapshot`.
   - `files.*` (6): `read_file`, `list_directory`, `search_files`, `file_info`, `write_file`, `delete_file` (with `PathGuard` sandbox protection).
4. **Centralized Pre-Execution Security**:
   - The 3-tier security model (`SAFE`, `ASK_USER`, `BLOCK`) is centralized in `PermissionEngine`. Agents and tools must never bypass or evaluate their own permissions.

---

## Mandatory Development Rules for Next Agent
1. **Virtual Environment**: Always use `C:\Users\vishnuu\Projects\TOM\.venv`. Never pollute external Python environments.
2. **Phase Boundary Discipline**: Implement only the active milestone.
3. **Reuse Existing Infrastructure**: Use `ToolRegistry`, `ToolExecutor`, `PermissionEngine`, `EngineClient`, and `LifecycleManager`. Do not create parallel pipelines.
4. **Hardware Budget**: Strict adherence to 8GB VRAM (RTX 4060) and 16GB system RAM.
5. **Phase Closeout Rule**: Upon completing the final iteration of Phase 4, execute the phase exit gate, update `STATE.md`/`PROGRESS.md`/`HANDOFF.md`, clean up phase-specific plans, and verify repository sanity before moving to Phase 5.

---

## Authoritative Reference for Next Session
- **Master Roadmap**: `IMPLEMENTATIONPLAN.md` (§ Phase 4: Agent Framework & Local Model Routing).
- **Exact Starting Task**: **Phase 4, Iteration 1 — Agent Definition, Loop & State Machine**:
  - Implement base Agent class, state machine (`IDLE`, `THINKING`, `ACTING`, `WAITING_CONFIRMATION`, `ERROR`, `TERMINATED`).
  - Wire `ToolExecutor` and `ToolRegistry` into the agent decision loop.
  - Implement prompt formatting and deterministic message history.