# TOM Current State Snapshot

## Current Status
- **Current Phase**: Phase 3 — Deterministic Tools & Permission Engine (**PLANNED / NEXT**)
- **Completed Phases**:
  - **Phase 0 (Bootstrap)**: COMPLETE (Configuration, logging, schemas, project scaffold).
  - **Phase 1 (Rust Engine)**: COMPLETE — 10/10 iterations, 79 tests passing. Key retained decisions: Windows Named Pipe IPC (`\\.\pipe\tom-engine`), Tokio async runtime, `CancellationToken` lifecycle, bounded request (500ms) and shutdown (5s) timeouts, structured JSON logging, bounded resources.
  - **Phase 2 (Python Core & IPC Client)**: COMPLETE — 8/8 iterations, 168 tests passing (153 unit, 15 integration). Full dual-core stack verified with P95 latency 0.359 ms.
- **Phase 3 Status**: **PLANNED / NOT STARTED** (6 iterations detailed in `PHASE3_IMPLEMENTATIONPLAN.md`).
- **Development Environment**: `.venv` at `C:\Users\vishnuu\Projects\TOM\.venv` (Python 3.11.9, pytest 9.1.1, ruff 0.16.7).

## Mandatory Development Rules & Constraints
1. **Python Virtual Environment Isolation (`.venv`)**:
   - Always install, test, and run within `C:\Users\vishnuu\Projects\TOM\.venv`.
   - Never install into global Python or other project environments.
   - Verify interpreter before package installations: `python -c "import sys; print(sys.executable)"` must resolve to `.venv`.
   - Preferred install command: `.\.venv\Scripts\python.exe -m pip install <package>`.
2. **Strict Dependency Discipline**:
   - Only install dependencies strictly required by the active phase (do not install Phase 4+ model/VLM/STT dependencies prematurely).
   - Prefer Python standard library or existing dependencies (`pydantic`, `psutil`, `httpx`, `pyyaml`).
3. **LLM/Agent Independence for Tools**:
   - Tools are deterministic, typed Python capabilities callable directly by code (`await executor.execute(...)`).
   - Tools must **never** import or directly depend on LLMs, models, routers, prompts, agents, or inference runtimes (LM Studio, Qwen, etc.).
   - The LLM/agent layer is a **consumer** of the tool system, not a dependency of tools.
4. **Centralized Pre-Execution Permission Enforcement**:
   - The 3-tier permission model (`SAFE`, `ASK USER`, `BLOCK`) is evaluated centrally by `PermissionEngine` *before* tool execution.
   - Individual tools do not evaluate their own permissions.
   - `BLOCK` operations (arbitrary shell, format, rmdir /s) are strictly denied; `ASK USER` requires explicit user confirmation via `ConfirmationHook`.

## Architecture Essentials
- **Dual-Core Design**: Python Brain (orchestration, agents, tools, memory) + Rust Engine (`tom-engine`: audio, hardware sensors, global hotkeys, IPC server).
- **Tool Architecture**: `ToolRegistry` (catalog/schemas) → `PermissionEngine` (pre-execution policy check) → `ToolExecutor` (validation, timeout, cancellation) → Deterministic Tools (`system`, `files`).
- **Wire Contract**: Protocol version 1 (`version = 1`). Windows Named Pipe `\\.\pipe\tom-engine` with newline-delimited JSON (`\n`) bounded by 1MB.
- **Resource Constraints**: Strictly bounded to 8GB VRAM (RTX 4060) and 16GB system RAM. Whisper STT defaults to CPU int8.

## Critical Constraints & Active Decisions
- **Decisions 001–005**: 15-phase roadmap (`IMPLEMENTATIONPLAN.md`), Windows Named Pipes, strict Pydantic v2 validation, SQLite + Qdrant memory separation, Whisper CPU placement.
- **Decisions 006–010**: Incremental iterations, 5s bounded shutdown, protocol v1 with `skip_serializing_if` optionals, 1MB NDJSON framing, 500ms server-side request timeout.
- **Decisions 011–016**: Async `SystemMonitor` mutex with throttled refresh, graceful degradation for GPU/battery, non-blocking broadcast `EventBus`, headless audio mock seams, typed `HotkeyAction`, < 2ms IPC latency benchmark.
- **Decisions 017–019**: Python Pydantic `extra="forbid"` on requests and `extra="ignore"` on responses, stdlib asyncio Windows named pipe transport, single background receive loop with UUID4 correlation.
- **Decision 020**: Timeouts & Cancellation: `IpcConfig.request_timeout_ms` (5000ms default) converted to seconds; `asyncio.shield` prevents cancellation of underlying pending Futures when caller task is cancelled; `finally` cleans `_pending` to prevent leaks; late responses safely discarded by receive loop; `InvalidStateError` guard prevents race conditions.
- **Decision 021**: Reconnection State Machine: `max_reconnect_attempts=0` means unlimited; pending requests fail immediately with `ConnectionLostError` on disconnect; zero-delay loops yield via `asyncio.sleep(0)`.
- **Decision 022**: High-Level Engine Client: `EngineClient` is a pure thin wrapper — no IPC mechanics; all methods call `NamedPipeIpcClient.request()` and return typed Pydantic models; GPU/Battery use flat models with all hardware fields `Optional` to accommodate Rust untagged enum serialization without discriminators.
- **Decision 023**: Lifecycle Coordinator: `LifecycleManager` coordinates startup (`connect() -> ping() -> status() -> ready`) and shutdown (`stop()` is strictly idempotent; signal handling falls back safely on Windows Proactor event loops; all startup errors raise `LifecycleError` with auto-cleanup).
- **Decision 024**: Integration Hardening: End-to-end integration verified against live `tom-engine`; benchmark P95 latency 0.359 ms (target < 10ms); task and resource leak audits confirmed clean.
- **Decision 025**: Phase 3 Architecture: 6-iteration modular plan established in `PHASE3_IMPLEMENTATIONPLAN.md`; tools are strictly decoupled from LLMs; 3-tier permission model enforced pre-execution; sandboxed file tools enforce path canonicalization and directory whitelisting.

## Current Test Baseline
- **Python**: **168 passed** (153 unit in `pytest tests/unit/ -v`, 15 integration in `pytest tests/integration/ -v`).
- **Rust**: **79 passed** (`cargo test` in `rust/tom-engine`).
- **Total Verified Tests**: **247 passed**.
- **IPC Benchmark**: P50: 0.144 ms, P95: 0.359 ms (Target: < 10.0 ms), P99: 0.448 ms.
- **Lint / Format**: Clean (`ruff check python tests`, `ruff format --check python tests`, `cargo clippy`, `cargo fmt --check`).

## Authoritative Documentation
- Implementation Roadmap: `IMPLEMENTATIONPLAN.md`
- Phase 2 Plan (Complete): `PHASE2_IMPLEMENTATIONPLAN.md`
- Phase 3 Plan (Approved): `PHASE3_IMPLEMENTATIONPLAN.md`

## Immediate Next Action
- Next development session: Begin **Phase 3, Iteration 1 — Tool Definition, Base Models & Tool Registry** (`python/tom/tools/registry.py`, `@tool` decorator, `tests/unit/tools/test_registry.py`).
