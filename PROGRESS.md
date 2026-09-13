# TOM Development Progress Ledger

## Phase 0 — Foundation / Project Bootstrap
- **Objective**: Repository scaffold, configuration system, structured logging, packaging, and tooling.
- **Key Result**: Complete 15-phase roadmap, directory tree, `pyproject.toml`, Pydantic v2 schemas (`schemas/config.py`), YAML loader (`core/config.py`), and JSON logger with secrets redactor (`telemetry/logging.py`).
- **Tests**: 9 passed (`pytest tests/unit/ -v`).
- **Decisions**: 8GB VRAM cap for RTX 4060; Whisper STT defaulted to CPU; mandatory secret redaction; 3-tier permission model.

---

## Phase 1 — Rust Engine (`tom-engine`)
**Milestone Result**: Complete 10/10 iterations verified. 79 tests passing (`cargo test`). IPC roundtrip latency 43.69 µs (< 2ms target); shutdown under load 11.67 ms (< 5s target).

- **Iteration 1 (Bootstrap)**: Tokio 1.43, tracing JSON logging, crate scaffold. (2 tests)
- **Iteration 2 (Lifecycle)**: `EngineLifecycle`, `CancellationToken`, signal handling, 5s shutdown timeout. (6 tests)
- **Iteration 3 (IPC Protocol)**: `IpcRequest`, `IpcResponse`, `IpcError`, protocol v1, Serde serialization. (16 tests)
- **Iteration 4 (Named Pipe Transport)**: Windows Named Pipe `\\.\pipe\tom-engine`, NDJSON framing, 1MB limit. (24 tests)
- **Iteration 5 (Dispatcher & Handlers)**: `IpcDispatcher`, 500ms handler timeout, `engine.ping`, `engine.status`. (30 tests)
- **Iteration 6 (System Telemetry)**: CPU, memory, NVML GPU, Win32 battery, disk, process monitoring via `sysinfo`. Graceful fallback on absent hardware. (50 tests)
- **Iteration 7 (Event Bus)**: `tokio::sync::broadcast` internal `EventBus`, typed `EngineEvent`s. (54 tests)
- **Iteration 8 (Audio Foundation)**: CPAL device enumeration, ring-buffer FIFO, synthetic capture/playback mock seams. (63 tests)
- **Iteration 9 (Input Foundation)**: Typed `HotkeyAction`, isolated Windows message pump, coordinate clamping. (72 tests)
- **Iteration 10 (Integration Hardening & Exit Gate)**: Concurrency stress (100 reqs), leak audit, latency benchmark (43.69 µs). (79 tests)

---

## Phase 2 — Python Core & IPC Client

### Iteration 1 — Protocol Models & Errors
- **Objective**: Implement strongly typed Pydantic v2 protocol schemas and exception taxonomy mirroring Rust engine IPC protocol v1.
- **Key Result**: `python/tom/ipc/protocol.py` (`IpcRequest`, `IpcResponse`, `IpcError`, `ErrorCode`, `ConnectionState`, `new_request_id`) and `python/tom/ipc/errors.py` (complete exception hierarchy).
- **Tests**: 49 passed (+40 tests).
- **Decisions**: `extra="forbid"` on outbound requests, `extra="ignore"` on inbound responses; native Python 3.11 `StrEnum` for error codes.

### Iteration 2 — Transport Layer
- **Objective**: Windows Named Pipe transport (`NamedPipeTransport`) with newline-delimited framing and `TransportProtocol` interface.
- **Key Result**: `python/tom/ipc/transport.py` utilizing `asyncio.ProactorEventLoop.create_pipe_connection` with 1MB bounds enforcement and EOF handling.
- **Tests**: 73 passed (+24 tests).
- **Decisions**: Ordered exception handling for Python 3.11 `TimeoutError` subclass of `OSError`; 0.5s bounded `wait_closed` on close.

### Iteration 3 — IPC Client Core
- **Objective**: Core asynchronous IPC client (`NamedPipeIpcClient`) with request correlation and single background receive loop.
- **Key Result**: `python/tom/ipc/client.py` managing `_pending` Future registry, UUID4 request correlation, remote error mapping, and disconnect teardown.
- **Tests**: 87 passed (+14 tests).
- **Decisions**: Single receive loop avoids multi-reader race conditions; unknown/duplicate responses discarded with warning without crashing.

### Iteration 4 — Timeouts & Cancellation
- **Objective**: Enforce per-request timeouts, implement `asyncio.shield` cancellation semantics, and harden edge case cleanup.
- **Key Result**: `NamedPipeIpcClient.request` enforces `IpcConfig.request_timeout_ms` (raising `IpcTimeoutError`), shields underlying Futures against caller cancellation, cleans `_pending` deterministically, safely discards late responses in `_receive_loop`, protects against `InvalidStateError` races, and isolates concurrent requests.
- **Tests**: 96 passed (+9 tests).
- **Decisions**: Decision 020: Use builtin `TimeoutError` (Python 3.11 UP041 compliance); `asyncio.shield` ensures caller cancellation does not corrupt underlying response futures; zero leaked pending requests.

### Iteration 5 — Reconnection State Machine
- **Objective**: Make `NamedPipeIpcClient` automatically reconnect after connection loss using exponential backoff with full jitter.
- **Key Result**: `_reconnect_loop()` in `client.py`; `ConnectionState` transitions (`DISCONNECTED → CONNECTING → CONNECTED → RECONNECTING → CLOSED`); backoff params from `IpcConfig`; `MaxRetriesExceededError` on exhaustion; `close()` cancels reconnect task before receive task; zero-delay loops yield via `asyncio.sleep(0)`.
- **Tests**: 105 passed (+9 tests).
- **Decisions**: Decision 021: `max_reconnect_attempts=0` means unlimited; pending requests fail immediately with `ConnectionLostError` on disconnect (no queuing); zero-delay loops must yield to avoid event loop starvation.

### Iteration 6 — High-Level Engine Client API
- **Objective**: Expose a clean typed Python API over the raw IPC client so callers receive Pydantic models, not dictionaries.
- **Key Result**: `python/tom/core/engine.py` (`EngineClient` wrapping `NamedPipeIpcClient`); 11 typed response models added to `protocol.py` (`PingResponse`, `StatusResponse`, `CpuInfo`, `MemoryInfo`, `GpuInfo`, `BatteryInfo`, `DiskVolume`, `DiskInfo`, `ProcessItem`, `ProcessInfo`, `SystemSnapshot`); `tests/unit/ipc/test_engine_client.py` (36 tests).
- **Tests**: 141 passed (+36 tests).
- **Decisions**: Decision 022: GPU/Battery use flat Pydantic models with all hardware fields `Optional` (not Pydantic discriminated unions) to match Rust untagged enum flat JSON serialization without complexity; `EngineClient` never duplicates IPC mechanics; all IPC exceptions propagate unchanged.

### Iteration 7 — Lifecycle Coordinator
- **Objective**: Implement `LifecycleManager` coordinating Python startup, readiness verification, and graceful shutdown.
- **Key Result**: `python/tom/core/lifecycle.py` (`LifecycleManager` with startup sequence: logging → IPC client → EngineClient → `connect()` → `ping()` → `status()` → log ready; idempotent `stop()`; Windows-compatible signal handling in `run_until_shutdown()`; `engine` property; cleanup on startup error); exported from `tom.core`; `tests/unit/ipc/test_lifecycle.py` (12 tests).
- **Tests**: 153 passed (+12 tests).
- **Decisions**: Decision 023: Startup errors clean up partially initialized resources and always raise `LifecycleError`; `stop()` is strictly idempotent; signal handling falls back safely to `signal.signal` when `loop.add_signal_handler` is unsupported by Windows Proactor loop.

### Iteration 8 — Integration Hardening & Phase 2 Exit Gate
- **Objective**: End-to-end validation against the real Rust engine (`tom-engine`), concurrency stress, resource audits, benchmark, and exit gate compliance.
- **Key Result**: `tests/integration/python_rust/test_live_ipc.py` (13 tests verifying live `ping`, `status`, `cpu`, `memory`, `gpu`, `battery`, `disk`, `processes`, `snapshot`, 10 concurrent requests, remote error mapping, and zero leaked tasks); `tests/integration/python_rust/test_lifecycle.py` (2 tests for live lifecycle start/stop and repeated 2-cycle execution); `tests/integration/python_rust/bench_ipc.py` (100-request benchmark reporting P50 0.144 ms, P95 0.359 ms vs 10ms target); `tests/integration/python_rust/conftest.py` (live engine fixture).
- **Tests**: 168 passed (153 unit + 15 integration).
- **Decisions**: Decision 024: Benchmark confirms Python IPC client overhead is negligible (< 0.4 ms P95); integration suite gracefully auto-starts or skips `tom-engine`; Phase 2 exit gate criteria 100% satisfied.

---

## Phase 2 — Final Summary
**Milestone Result**: Phase 2 COMPLETE (8/8 iterations verified).
- Python unit tests: 153 passed.
- Python integration tests: 15 passed.
- Total Python tests: 168 passed.
- Rust tests: 79 passed (untouched).
- Total verified tests: 247 passed.
- Roundtrip P95 latency: 0.359 ms (target < 10.0 ms).
- Ruff check and format: 0 errors / 0 diffs.
- Cargo clippy and fmt: 0 warnings / 0 diffs.
- Ready for Phase 3 (Deterministic Tools & Permission Engine).

---

## Phase 3 — Deterministic Tools & Permission Engine (PLANNING)
- **Objective**: Establish deterministic, typed, testable tool execution layer with 3-tier pre-execution permission engine (`SAFE`, `ASK USER`, `BLOCK`), path-traversal guards, and sub-millisecond dispatch overhead.
- **Planning Milestone**: Created `PHASE3_IMPLEMENTATIONPLAN.md` with 6 detailed iterations:
  - Iteration 1: Tool Definition, Base Models & Tool Registry (`python/tom/tools/registry.py`)
  - Iteration 2: Security & Permission Engine (`python/tom/security/permissions.py`, `confirmation.py`)
  - Iteration 3: Tool Executor (Pre-Execution Validation, Timeouts & Cancellation) (`python/tom/tools/executor.py`)
  - Iteration 4: Deterministic System Tools (`python/tom/tools/system.py`)
  - Iteration 5: Sandboxed File Tools & Path-Traversal Guards (`python/tom/tools/files.py`)
  - Iteration 6: Tool System Integration, End-to-End Pipeline & Phase 3 Exit Gate
- **Key Rules Established**:
  - Rule 1: Python environment strictly bounded to `.venv` (`C:\Users\vishnuu\Projects\TOM\.venv`).
  - Rule 2: Strict dependency discipline; only install what is actively required.
  - Rule 3: LLM/Agent independence: Tools are deterministic Python capabilities and must never import or depend on LLMs, models, routers, prompts, or agents.
  - Rule 4: Centralized pre-execution permission enforcement: `PermissionEngine` evaluates `SAFE` / `ASK USER` / `BLOCK` prior to any execution.




