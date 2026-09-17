# TOM Current State Snapshot

## Current Status
- **Current Phase**: Phase 3 — Deterministic Tools & Permission Engine (**CLOSED & COMPLETE — 6/6 Iterations Complete**)
- **Next Target**: **Phase 4 — Agent Framework & Local Model Routing**
- **Completed Phases & Milestones**:
  - **Phase 0 (Bootstrap)**: Project scaffold, Pydantic schemas, YAML config loader, JSON logging with secret redactor.
  - **Phase 1 (Rust Engine)**: Always-on `tom-engine`, Windows Named Pipe IPC (`\\.\pipe\tom-engine`), Tokio async runtime, hardware telemetry, audio & input foundations, 79 tests passing.
  - **Phase 2 (Python Core & IPC Client)**: Async IPC client, protocol v1 models/errors, reconnection state machine, high-level `EngineClient`, `LifecycleManager`, 168 tests passing (P95 roundtrip 0.359 ms).
  - **Phase 3 (Tools & Security)**:
    - Iteration 1: `ToolDefinition`, `ToolResult`, `PermissionLevel`, `ToolRegistry`, `@tool` decorator (39 unit tests).
    - Iteration 2: `PermissionEngine`, `PermissionDecision`, `ConfirmationHook` implementations (33 unit tests).
    - Iteration 3: `ToolExecutor` (lookup, permissions, confirmation, Pydantic validation, timeouts, cancellation, monotonic timing) (22 unit tests).
    - Iteration 4+5 (Combined): Deterministic System Tools (`system.py`, 7 tools, 16 tests) + Sandboxed File Tools with `PathGuard` (`files.py`, 6 tools, 17 tests).
    - Iteration 6: Tool bootstrap seam (`bootstrap.py`), end-to-end integration tests (`test_tool_pipeline.py`, 32 tests), Phase 3 Exit Gate passed.
- **Environment**: Dedicated `.venv` at `C:\Users\vishnuu\Projects\TOM\.venv` (Python 3.11.9, pytest 9.1.1, ruff 0.16.7).

## Mandatory Constraints & Rules
1. **Virtual Environment Isolation**: Always use `C:\Users\vishnuu\Projects\TOM\.venv`. Never pollute global or external Python environments. Verify `sys.executable` before installs.
2. **Dependency Discipline**: Strictly install dependencies required by the active phase. Never install Phase 4+ dependencies (models, PyTorch, llama-cpp, Qdrant) prematurely.
3. **LLM/Agent Independence for Tools**: Tools are deterministic, typed Python capabilities. They must never import, call, or depend on LLMs, prompts, routers, Qwen, LM Studio, or agents.
4. **Centralized Pre-Execution Permission Enforcement**: The 3-tier security model (`SAFE`, `ASK_USER`, `BLOCK`) is evaluated centrally by `PermissionEngine` *before* execution. Individual tools never evaluate their own safety. `BLOCK` operations are unconditionally denied; `ASK_USER` requires explicit confirmation via `ConfirmationHook`.
5. **Hardware & Resource Limits**: Strictly bounded to 8GB VRAM (RTX 4060) and 16GB system RAM. Whisper STT defaults to CPU int8.
6. **Phase Closeout Convention**: At the completion of the final iteration of every phase:
   1. Run phase exit gate.
   2. Update `STATE.md`, `PROGRESS.md`, and `HANDOFF.md`.
   3. Migrate any unique historical context from the phase implementation plan.
   4. Delete the completed phase implementation plan file.
   5. Verify deletion and purge stale references.
   6. Verify repository sanity and mark the phase CLOSED.
   7. Only then begin the next phase in a subsequent session.

## Architecture Essentials
- **Dual-Core Stack**: Python Brain (orchestration, agents, tools, memory) + Rust Engine (`tom-engine`: hardware sensors, audio, hotkeys, IPC server).
- **Tool Pipeline**: Request → `ToolRegistry` (lookup & schema) → `PermissionEngine` (pre-execution policy check) → `ConfirmationHook` (if ASK_USER) → `ToolExecutor` (Pydantic validation, timeout, cancellation) → Deterministic Tools (`system`, `files`) → `ToolResult`.
- **Tool Bootstrap Seam**: `tom.tools.bootstrap.setup_default_tools(registry, ...)` registers all 13 built-in tools (7 system + 6 file) into any given registry or `default_registry`.
- **IPC Protocol**: Protocol v1, Windows Named Pipe `\\.\pipe\tom-engine`, NDJSON framing bounded by 1MB.

## Key Active Architectural Decisions
- **001–005**: 15-phase roadmap, Windows Named Pipes, strict Pydantic v2 validation, SQLite + Qdrant memory separation, Whisper CPU placement.
- **006–010**: Incremental iterations, 5s bounded shutdown, protocol v1 NDJSON framing, 500ms server-side request timeout.
- **011–016**: Throttled async `SystemMonitor` mutex, graceful telemetry degradation, broadcast `EventBus`, audio/input mock seams.
- **017–019**: Request `extra="forbid"` and response `extra="ignore"`, asyncio named pipe transport, single background receive loop with UUID4 correlation.
- **020–022**: Builtin `TimeoutError` and `asyncio.shield` cancellation protection, backoff reconnection state machine, thin `EngineClient` returning flat Pydantic models.
- **023–024**: Idempotent `LifecycleManager`, clean task audits, live IPC benchmark < 0.4 ms P95 (target < 10ms).
- **025**: Phase 3 6-iteration modular plan; path-traversal guards and directory whitelisting for file tools.
- **026**: `ToolDefinition`, `ToolResult`, `PermissionLevel`, `ToolRegistry`, and `@tool` decorator. Dynamic schema extraction via `create_model`.
- **027**: Centralized `PermissionEngine` with blocklist scanning (unconditional `BLOCK`), tool-specific policy overrides, and decoupled `ConfirmationHook` implementations defaulting to denial on timeout.
- **028**: `ToolExecutor` strictly enforces linear pipeline: Registry Lookup → Permission Check → Confirmation → Argument Validation → Timeout/Cancellation Invocation → `ToolResult`. Async/sync tools seamlessly supported; monotonic `time.perf_counter()` timing; inner execution tasks cleanly cancelled and awaited with zero task leaks on timeout or caller cancellation.
- **029**: Combined System Tools & Sandboxed Filesystem Architecture — zero new dependencies, stdlib/psutil fallback, strict `PathGuard` security boundary, `ASK_USER` for all mutations.
- **030**: Canonical Tool Registration Seam via `tom.tools.bootstrap.setup_default_tools` — registers built-in tools into any target registry without introducing LLM or agent dependencies.

## Current Test Baseline
- **Python Unit**: **280 passed** (`pytest tests/unit/ -v`).
- **Python Integration**: **47 passed** (`pytest tests/integration/ -v`).
- **Total Python**: **327 passed**.
- **Rust Engine**: **79 passed** (`cargo test` in `rust/tom-engine`).
- **Total Verified Tests**: **406 passed** (+32 since Iteration 4+5).
- **Quality Gates**: Ruff clean (0 violations, 0 diffs), Cargo clippy/fmt clean.

## Immediate Next Task
- **Phase 4 — Agent Framework & Local Model Routing** (Iteration 1: Agent Definition, Loop & State Machine).
