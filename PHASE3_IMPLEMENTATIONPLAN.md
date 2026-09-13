# TOM Phase 3 — Deterministic Tools & Permission Engine
## Implementation-Ready Architecture & Iteration Plan

**Author:** Principal Systems & Security Architect  
**Baseline:** Phase 1 (Rust Engine) and Phase 2 (Python Core & IPC Client) COMPLETE & VERIFIED.  
**Verified Tests:** 168 Python passed (153 unit, 15 integration) | 79 Rust passed | 247 total verified tests.  
**Status:** PLANNING DOCUMENT — Approved for Phase 3 Execution.

---

## 1. Phase 3 Objective

> Implement a deterministic, typed, testable, timeout-bounded, and cancellable Python tool execution engine governed by an authoritative 3-tier permission model (`SAFE`, `ASK USER`, `BLOCK`), completely independent of the LLM/agent layer.

At the end of Phase 3, TOM will be able to:
1. Register, discover, and inspect deterministic tools via `ToolRegistry`.
2. Automatically generate Pydantic v2 schemas and JSON Schema tool specifications for future LLM consumers.
3. Centralize security evaluation in `PermissionEngine` before any tool executes.
4. Prompt for user confirmation via `ConfirmationHook` when an `ASK USER` tool is invoked.
5. Unconditionally reject `BLOCK` operations (destructive commands, arbitrary shell).
6. Execute tools asynchronously with timeout deadlines (`ToolsConfig.default_timeout_seconds`) and cooperative cancellation support via `ToolExecutor`.
7. Query hardware and system telemetry via deterministic System tools wrapping Phase 2's `EngineClient`.
8. Safely inspect, search, read, write, and manage files through Sandboxed File tools with strict directory whitelisting and path-traversal guards.
9. Execute all operations with sub-millisecond dispatch overhead (< 1ms).

---

## 2. Mandatory Architectural & Development Rules

### Rule 1: Python Virtual Environment Isolation (`.venv`)
TOM has a dedicated virtual environment at:
```text
C:\Users\vishnuu\Projects\TOM\.venv
```
* **Always use TOM's `.venv`**: Whenever installing, upgrading, removing, or testing Python packages for TOM, the active Python interpreter must be `C:\Users\vishnuu\Projects\TOM\.venv\Scripts\python.exe`.
* **Verify `sys.executable` before installing**:
  ```powershell
  python -c "import sys; print(sys.executable)"
  ```
* **Never pollute global Python or other virtualenvs**: Never install TOM dependencies globally or into another project's virtualenv.
* **Preferred installation syntax**:
  ```powershell
  .\.venv\Scripts\python.exe -m pip install <package>
  ```

### Rule 2: Dependency Management Discipline
* Do not blindly install packages. Before adding any dependency, verify whether the standard library or an existing dependency (`pydantic`, `psutil`, `httpx`, `pyyaml`) suffices.
* Only install dependencies strictly required by the active phase.
* Do not install future Phase 4+ dependencies (models, PyTorch, llama-cpp, VLM, STT, TTS, Qdrant) prematurely.

### Rule 3: Tools Must Not Know About The LLM (LLM/Agent Independence)
* Tools are deterministic, typed Python capabilities callable directly by Python code:
  ```python
  result = await executor.execute("system.cpu_info", {})
  ```
* **Zero LLM dependencies**: Tools must NEVER import, call, or depend on:
  * LLM clients
  * Model routers or prompts
  * Qwen or other AI model runtimes
  * LM Studio or local inference engines
  * Agents or agent frameworks
* The future agent/model layer is a **consumer** of the tool system, not a dependency of the tool system.

### Rule 4: Centralized Pre-Execution Permission Enforcement
* The 3-tier permission model (`SAFE`, `ASK USER`, `BLOCK`) is evaluated **before** execution by `PermissionEngine`.
* Individual tools do NOT determine their own safety or bypass the security boundary.
* The execution pipeline is strictly linear and non-bypassable:
  ```text
  Tool Invocation Request
            │
            ▼
      Tool Registry (verify existence & extract schema)
            │
            ▼
    Permission Engine (evaluate SAFE / ASK USER / BLOCK)
            │
            ├─► BLOCK ──► Raise PermissionDeniedError (Execution halted)
            ├─► ASK USER ──► ConfirmationHook ──► If denied: Cancelled
            └─► SAFE
                    │
                    ▼
          Pydantic Argument Validation
                    │
                    ▼
          Timeout & Cancellation Wrapper
                    │
                    ▼
              Tool Executor
                    │
                    ▼
          Deterministic Tool Execution
                    │
                    ▼
          Structured ToolResult
  ```

---

## 3. Subsystem Architecture

```text
                        TOM Python Core
                              │
                              ▼
                     python/tom/tools/
                     ├── registry.py      <- ToolRegistry & @tool decorator
                     ├── executor.py      <- ToolExecutor (timeout, validation, cancel)
                     ├── system.py        <- Deterministic System Tools (EngineClient)
                     └── files.py         <- Sandboxed File Tools (Path traversal guards)
                              │
                              ▼
                    python/tom/security/
                     ├── permissions.py   <- PermissionEngine (SAFE / ASK / BLOCK)
                     └── confirmation.py  <- ConfirmationHook (interactive prompt)
```

---

## 4. Permission Taxonomy & Security Model

```python
class PermissionLevel(StrEnum):
    SAFE = "SAFE"          # Read-only, informational, non-destructive -> Auto-executed
    ASK_USER = "ASK_USER"  # State-modifying, file modification, process kill -> Requires explicit confirmation
    BLOCK = "BLOCK"        # Arbitrary shell, disk formatting, credential extraction -> Always prohibited
```

| Permission Level | Description | Examples |
|---|---|---|
| `SAFE` | Automatic execution permitted without prompting | Query CPU/RAM/GPU, read whitelisted files, list directories, search files |
| `ASK_USER` | Explicit user confirmation required prior to execution | Write file, delete file, kill process, change system state |
| `BLOCK` | Execution permanently prohibited via tool engine | Arbitrary shell execution (`rmdir /s`, `format`, `diskpart`, `reg delete`), credential access |

---

## 5. Phase 3 Detailed Iteration Breakdown

### Iteration 1 — Tool Definition, Base Models & Tool Registry
* **Objective**: Create the core tool abstraction, strongly typed Pydantic models for definitions and results, and the central registry with `@tool` decorator.
* **Target Files**:
  * `python/tom/tools/registry.py`
  * `tests/unit/tools/test_registry.py`
* **Components**:
  * `ToolDefinition`: Pydantic model (`name`, `description`, `category`, `input_schema`, `output_schema`, `permission_level`, `timeout_seconds`).
  * `ToolResult`: Pydantic model (`success: bool`, `data: Any`, `error: str | None`, `execution_time_ms: float`).
  * `ToolRegistry`: Class managing registration, lookup, duplicate checks, catalog listing, and JSON Schema export for LLM function calling.
  * `@tool` decorator: Easy declaration of typed tools with automated schema extraction.
* **Exit Criteria**: All unit tests pass, schemas serialize cleanly, duplicate registrations error predictably.

---

### Iteration 2 — Security & Permission Engine
* **Objective**: Implement the authoritative 3-tier security classification engine and user confirmation interface.
* **Target Files**:
  * `python/tom/security/permissions.py`
  * `python/tom/security/confirmation.py`
  * `tests/unit/security/test_permissions.py`
* **Components**:
  * `PermissionLevel`: `SAFE`, `ASK_USER`, `BLOCK`.
  * `PermissionDecision`: Pydantic model (`allowed: bool`, `requires_confirmation: bool`, `reason: str`).
  * `PermissionEngine`: Policy evaluator checking `PermissionsConfig`, blocked command lists, path restrictions, and tool default classifications.
  * `ConfirmationHook`: Abstract base class and `ConsoleConfirmationHook` / `CallbackConfirmationHook` for interactive approval flows.
* **Exit Criteria**: `BLOCK` operations reliably denied, `ASK_USER` correctly requests confirmation, `SAFE` bypasses confirmation, timeout on confirmation defaults to denial.

---

### Iteration 3 — Tool Executor (Pre-Execution Validation, Timeouts & Cancellation)
* **Objective**: Build the asynchronous tool execution engine coordinating registry lookup, pre-execution permission evaluation, argument validation, timeout bounding, and cancellation.
* **Target Files**:
  * `python/tom/tools/executor.py`
  * `tests/unit/tools/test_executor.py`
* **Components**:
  * `ToolExecutor`: Orchestrates execution:
    1. Tool lookup in `ToolRegistry`.
    2. Centralized permission check in `PermissionEngine`.
    3. Confirmation prompt if required.
    4. Input argument validation via Pydantic model.
    5. Execution under `asyncio.wait_for` timeout.
    6. Graceful cancellation handling and execution timing.
    7. Output validation and structured `ToolResult` packaging.
* **Exit Criteria**: Hanging tools cleanly abort after timeout, cancelled tasks do not leak, permission denials halt before invocation, validation errors produce structured failures.

---

### Iteration 4 — Deterministic System Tools
* **Objective**: Implement typed, deterministic system inspection tools leveraging Phase 2's `EngineClient` and standard library telemetry.
* **Target Files**:
  * `python/tom/tools/system.py`
  * `tests/unit/tools/test_system_tools.py`
* **Components**:
  * `system.cpu_info` (`SAFE`) — Core count, frequency, utilization.
  * `system.memory_info` (`SAFE`) — Total, used, and available RAM.
  * `system.gpu_info` (`SAFE`) — Utilization, VRAM, temperature (graceful fallback if absent).
  * `system.battery_info` (`SAFE`) — Battery percentage and charging status.
  * `system.disk_info` (`SAFE`) — Mounted volumes and capacity.
  * `system.list_processes` (`SAFE`) — Top processes ranked by CPU/memory.
  * `system.get_snapshot` (`SAFE`) — Aggregated system snapshot.
* **Exit Criteria**: All system tools return validated Pydantic models; tools function seamlessly both with live `EngineClient` and mock seams.

---

### Iteration 5 — Sandboxed File Tools & Path-Traversal Guards
* **Objective**: Implement secure file tools with directory whitelisting, canonical path verification, and traversal attack prevention.
* **Target Files**:
  * `python/tom/tools/files.py`
  * `tests/unit/tools/test_file_tools.py`
* **Components**:
  * Path Guard: Canonicalization via `Path.resolve()`, verification that target path starts within `ToolsConfig.allowed_directories`, rejection of `../`, Windows device names (`CON`, `PRN`, `NUL`), and hidden system directories.
  * `files.read_file` (`SAFE`) — Bounded by `ToolsConfig.max_file_read_bytes` (default 5MB).
  * `files.list_directory` (`SAFE`) — Directory listing with file metadata and size.
  * `files.search_files` (`SAFE`) — Pattern-based file search bounded by `max_search_results`.
  * `files.file_info` (`SAFE`) — File size, creation/modification timestamps, permissions.
  * `files.write_file` (`ASK_USER`) — Safe file writing with backup seam.
  * `files.delete_file` (`ASK_USER`) — Safe file deletion with explicit confirmation.
* **Exit Criteria**: Traversal attacks (`../../Windows/System32`) strictly blocked with `SecurityError`, file reads over 5MB rejected, write/delete require `ASK_USER` confirmation.

---

### Iteration 6 — Tool System Integration, End-to-End Pipeline & Phase 3 Exit Gate
* **Objective**: End-to-end integration of the complete tool ecosystem with live engine and live filesystem, performance audit, and Phase 3 verification.
* **Target Files**:
  * `tests/integration/tools/test_tool_pipeline.py`
  * `STATE.md`, `PROGRESS.md`, `HANDOFF.md`
* **Validation Tasks**:
  * Verify end-to-end pipeline: `Registry → PermissionEngine → ToolExecutor → Tool → ToolResult`.
  * Verify sub-millisecond tool dispatch overhead (< 1ms).
  * Comprehensive regression testing against Phase 1 (Rust 79/79 passed) and Phase 2 (Python IPC 168/168 passed).
  * Complete Phase 3 Exit Gate checklist.

---

## 6. Phase 3 Exit Gate Checklist

```
[ ] pytest tests/unit/ -v                              ALL PASS
[ ] pytest tests/integration/ -v                      ALL PASS
[ ] ruff check python/ tests/                          0 violations
[ ] ruff format --check python/ tests/                 0 diffs
[ ] cargo test (in rust/tom-engine)                    79/79 PASS
[ ] Tool dispatch overhead                             < 1.0 ms
[ ] Path traversal guard audit                         100% BLOCKED
[ ] Centralized permission check                       100% ENFORCED PRE-EXECUTION
[ ] Zero dependencies on LLM/model layer               VERIFIED
[ ] Python dependencies installed in .venv             VERIFIED
[ ] STATE.md, PROGRESS.md, HANDOFF.md updated          DONE
```
