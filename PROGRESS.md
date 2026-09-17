# TOM Development Progress Ledger

## Phase 0 — Foundation / Project Bootstrap (COMPLETE)
- **Objective**: Repository scaffold, configuration system, structured logging, packaging, and tooling.
- **Key Result**: 15-phase roadmap, directory tree, `pyproject.toml`, Pydantic v2 schemas (`schemas/config.py`), YAML loader (`core/config.py`), JSON logger with secret redactor (`telemetry/logging.py`).
- **Tests**: 9 passed.

---

## Phase 1 — Rust Engine (`tom-engine`) (COMPLETE)
- **Objective**: Always-on background engine for IPC, system telemetry, audio capture/playback, and global hotkeys.
- **Key Result**: 10/10 iterations complete. Windows Named Pipe server (`\\.\pipe\tom-engine`), Tokio async runtime, protocol v1 NDJSON framing, bounded shutdown (< 12 ms), P95 IPC latency 43.69 µs.
- **Tests**: 79 passed (`cargo test`).

---

## Phase 2 — Python Core & IPC Client (COMPLETE)
- **Objective**: Asynchronous Python IPC client communicating with `tom-engine`, typed models, reconnection, and lifecycle management.
- **Key Result**: 8/8 iterations complete. `NamedPipeIpcClient` with UUID4 correlation, timeout/cancellation shielding, exponential backoff reconnection, typed `EngineClient`, and `LifecycleManager`. Live P95 latency 0.359 ms (< 10ms target).
- **Tests**: 168 passed (153 unit + 15 integration).

---

## Phase 3 — Deterministic Tools & Permission Engine (CLOSED & COMPLETE — 6/6 Iterations)
- **Objective**: Deterministic, typed, testable tool execution layer with 3-tier pre-execution permission engine (`SAFE`, `ASK_USER`, `BLOCK`), path-traversal guards, sub-millisecond dispatch overhead, and complete pipeline integration.

### Completed Iterations:
- **Iteration 1 — Tool Definition, Base Models & Tool Registry**:
  - `python/tom/tools/registry.py`, `python/tom/tools/__init__.py`.
  - Implemented `ToolDefinition`, `ToolResult`, `PermissionLevel`, `ToolRegistry`, `@tool` decorator with automated schema extraction, and OpenAI/Qwen compatible JSON schema export.
  - Tests: 39 passed (`tests/unit/tools/test_registry.py`).
  - Decision 026: Tool system foundation strictly decoupled from LLM/agent layers.
- **Iteration 2 — Security & Permission Engine**:
  - `python/tom/security/permissions.py`, `python/tom/security/confirmation.py`, `python/tom/security/__init__.py`.
  - Implemented `PermissionEngine`, `PermissionDecision`, blocklist scanning (unconditional `BLOCK`), tool policy overrides, and `ConfirmationHook` (`ConsoleConfirmationHook`, `CallbackConfirmationHook`, testing hooks) defaulting to denial on timeout.
  - Tests: 33 passed (`tests/unit/security/test_permissions.py`).
  - Decision 027: Centralized pre-execution security boundary and safe failure defaults.
- **Iteration 3 — Tool Executor (Pre-Execution Validation, Timeouts & Cancellation)**:
  - `python/tom/tools/executor.py`, `python/tom/tools/__init__.py`.
  - Implemented `ToolExecutor` orchestrating registry lookup, centralized permission check, confirmation, Pydantic argument validation, timeout deadlines, clean cancellation with zero task leaks, monotonic execution duration measurement, and `ToolResult` packaging. Supports async and sync tools seamlessly.
  - Tests: 22 passed (`tests/unit/tools/test_executor.py`).
  - Decision 028: Tool execution pipeline, cancellation safety, and sub-millisecond dispatch.
- **Iteration 4+5 (Combined) — Deterministic System Tools & Sandboxed File Tools**:
  - `python/tom/tools/system.py`, `python/tom/tools/files.py`, `python/tom/tools/__init__.py`.
  - System tools: 7 tools (`system.cpu_info`, `system.memory_info`, `system.gpu_info`, `system.battery_info`, `system.disk_info`, `system.list_processes`, `system.get_snapshot`). Dual-mode: `EngineClient` delegation with graceful stdlib/psutil fallback. All `SAFE`.
  - File tools: 6 tools (`files.read_file`, `files.list_directory`, `files.search_files`, `files.file_info`, `files.write_file`, `files.delete_file`). `PathGuard` security barrier with traversal rejection (`..`), Windows reserved name blocking (`CON`, `PRN`, `AUX`, `NUL`, `COM1-9`, `LPT1-9`), `is_relative_to()` whitelist boundary, and 5MB read limit. Read ops `SAFE`, mutations `ASK_USER`.
  - Tests: 33 new (16 system + 17 file). Total Python unit: 280.
  - Decision 029: Combined System Tools & Sandboxed Filesystem Architecture — zero new dependencies, stdlib/psutil fallback, strict `PathGuard` security boundary, `ASK_USER` for all mutations.
- **Iteration 6 — Integration Pipeline & Exit Gate (Closeout)**:
  - `python/tom/tools/bootstrap.py`, `python/tom/tools/__init__.py`, `tests/integration/tools/test_tool_pipeline.py`.
  - Created canonical tool registration bootstrap seam `setup_default_tools` wiring all 13 built-in tools (7 system + 6 file) into any given registry or `default_registry`. Preserves empty-registry isolation (`is not None`) and supports idempotent re-registration. Zero LLM or agent dependencies.
  - Added 32 end-to-end integration tests in `tests/integration/tools/test_tool_pipeline.py` verifying full pipeline flow: Registry Lookup → PermissionEngine → ConfirmationHook → ToolExecutor → Deterministic Tool → ToolResult.
  - Verified:
    - Tool discovery for all 7 system tools and 6 file tools.
    - SAFE executions with zero confirmation overhead and sub-millisecond dispatch (< 0.35 ms average).
    - ASK_USER accept (mutations executed) and deny (operations halted pre-execution, files unchanged).
    - BLOCK policy enforcement and blocked parameter content detection.
    - Centralized Pydantic argument validation rejection before handler invocation.
    - Clean timeout cancellation with zero task leaks.
    - Consistent `ToolResult` structures.
    - `PathGuard` directory sandbox escape and `..` traversal rejection through live pipeline.
  - Decision 030: Canonical Tool Registration Seam (`setup_default_tools`).
  - Phase 3 Exit Gate: ALL CRITERIA PASSED.
  - Phase 3 formally marked CLOSED; execution artifacts cleaned up per Phase Closeout Rule.

### Final Verified Test Baseline:
- Python Unit: 280 passed | Python Integration: 47 passed | Rust: 79 passed | Total: 406 passed.
- Quality Gates: Ruff 0 violations / 0 diffs, Cargo clippy/fmt clean.

---

## Next Milestone
- **Phase 4 — Agent Framework & Local Model Routing**:
  - Iteration 1: Agent Definition, Loop & State Machine.
