# TOM Agent Handoff

## Current Position
- **Phase**: Phase 2 — Python Core & IPC Client (**COMPLETE**)
- **Phase 3 Status**: **PLANNED / READY TO EXECUTE** (Detailed specification in `PHASE3_IMPLEMENTATIONPLAN.md`)
- **Verified Test Baseline**:
  - Python Unit: 153 passed (`pytest tests/unit/ -v`)
  - Python Integration: 15 passed (`pytest tests/integration/ -v`)
  - Total Python: 168 passed
  - Rust: 79 passed (`cargo test` in `rust/tom-engine`)
  - Total Verified Tests: **247 passed**
  - Latency Benchmark: P50 = 0.144 ms, P95 = 0.359 ms (Target < 10.0 ms — **PASS**)
  - Quality Gates: Ruff clean (0 violations, 0 diffs), Cargo fmt clean, Cargo clippy clean (0 warnings)
- **Development Environment**:
  - Python: 3.11.9
  - Environment: Dedicated `.venv` at `C:\Users\vishnuu\Projects\TOM\.venv`
  - pytest: 9.1.1
  - Ruff: 0.16.7
- **Authoritative Plans**:
  - Master Roadmap: `IMPLEMENTATIONPLAN.md` (§ Phase 3)
  - Phase 3 Specification: `PHASE3_IMPLEMENTATIONPLAN.md`

---

## Phase 3 Objective & Architecture

**Goal**: Implement the deterministic tool system, tool registry, execution engine with timeouts and cancellation, Pydantic input/output schemas, and the 3-tier pre-execution permission model (`SAFE`, `ASK USER`, `BLOCK`), completely independent of the LLM/agent layer.

### Target Execution Sequence
```text
Tool Request
     │
     ▼
Tool Registry (registry.py)
     │
     ▼
Permission Engine (permissions.py) ──► SAFE / ASK USER / BLOCK
     │                                      │
     ▼                                      ▼ (if ASK USER: confirmation.py)
Pydantic Validation                         │
     │                                      ▼ (if BLOCK: abort immediately)
     ▼
Timeout & Cancellation Wrapper
     │
     ▼
Tool Executor (executor.py)
     │
┌────┴──────────────────────────┐
▼                               ▼
System Tools (system.py)        File Tools (files.py)
(EngineClient telemetry)        (Whitelisted, traversal-guarded)
```

---

## Phase 3 Planned Iterations

1. **Iteration 1 — Tool Definition, Base Models & Tool Registry**:
   - `python/tom/tools/registry.py`: `ToolDefinition`, `ToolResult`, `ToolRegistry`, `@tool` decorator, Pydantic v2 schema generation.
   - `tests/unit/tools/test_registry.py`.
2. **Iteration 2 — Security & Permission Engine**:
   - `python/tom/security/permissions.py`: `PermissionLevel` (`SAFE`, `ASK_USER`, `BLOCK`), `PermissionDecision`, `PermissionEngine`.
   - `python/tom/security/confirmation.py`: `ConfirmationHook` interface.
   - `tests/unit/security/test_permissions.py`.
3. **Iteration 3 — Tool Executor (Pre-Execution Validation, Timeouts & Cancellation)**:
   - `python/tom/tools/executor.py`: Pre-execution security check, argument validation, timeout deadlines (`ToolsConfig.default_timeout_seconds`), cancellation shielding, structured error normalization.
   - `tests/unit/tools/test_executor.py`.
4. **Iteration 4 — Deterministic System Tools**:
   - `python/tom/tools/system.py`: `cpu_info`, `memory_info`, `gpu_info`, `battery_info`, `disk_info`, `list_processes`, `get_snapshot` wrapping Phase 2 `EngineClient`.
   - `tests/unit/tools/test_system_tools.py`.
5. **Iteration 5 — Sandboxed File Tools & Path-Traversal Guards**:
   - `python/tom/tools/files.py`: Path canonicalization (`resolve()`), whitelist verification against `ToolsConfig.allowed_directories`, rejection of `../` traversal, byte bounds (`ToolsConfig.max_file_read_bytes`), read tools (`SAFE`), write/delete (`ASK_USER`).
   - `tests/unit/tools/test_file_tools.py`.
6. **Iteration 6 — Tool System Integration, End-to-End Pipeline & Phase 3 Exit Gate**:
   - `tests/integration/tools/test_tool_pipeline.py`: Full pipeline integration, sub-millisecond dispatch audit (< 1ms), quality gates, exit criteria validation.

---

## Mandatory Development & Architectural Rules

1. **Python Virtual Environment (`.venv`) Rule**:
   - All package installations, testing, and tool executions MUST use TOM's dedicated `.venv`:
     ```text
     C:\Users\vishnuu\Projects\TOM\.venv
     ```
   - Before installing any package, verify: `python -c "import sys; print(sys.executable)"` resolves to `.venv`.
   - Install packages using: `.\.venv\Scripts\python.exe -m pip install <package>`.
   - Never install packages globally or into unrelated virtual environments.
2. **Dependency Management Discipline**:
   - Only install dependencies strictly required by the active phase. Prefer standard library or existing dependencies (`pydantic`, `psutil`, `httpx`, `pyyaml`).
   - Do NOT install Phase 4+ dependencies (PyTorch, llama-cpp, VLM, STT, TTS, Qdrant) prematurely.
3. **LLM/Agent Independence for Tools**:
   - Tools are deterministic, typed Python capabilities callable directly from Python code:
     `result = await executor.execute("system.cpu_info", {})`
   - Tools must **never** import, invoke, or depend on: LLMs, model routers, prompts, Qwen, LM Studio, inference runtimes, or agent frameworks.
   - The future LLM/agent layer is a **consumer** of tools, not a dependency of tools.
4. **Centralized Pre-Execution Permission Enforcement**:
   - Security checks happen **before** tool execution via `PermissionEngine`.
   - Tools never evaluate their own safety.
   - `BLOCK` operations are strictly forbidden; `ASK USER` requires explicit user confirmation via `ConfirmationHook`.

---

## Exact First Task for Next Session

**Execute Phase 3, Iteration 1 — Tool Definition, Base Models & Tool Registry**:
- Target file: `python/tom/tools/registry.py`
- Test file: `tests/unit/tools/test_registry.py`
- Implement:
  1. `ToolDefinition` (name, description, category, input_schema, output_schema, permission_level, timeout_seconds)
  2. `ToolResult` (success, data, error, execution_time_ms)
  3. `ToolRegistry` (register, get, list_tools, export_json_schemas)
  4. `@tool` decorator extracting Pydantic v2 schemas automatically
- Verify with `.\.venv\Scripts\pytest.exe tests/unit/tools/test_registry.py -v`.

--- 