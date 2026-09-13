# TOM Phase 1 & Phase 2 — Testing & Verification Guide

> **Operating System:** Windows 11  
> **Terminal / Shell:** Windows PowerShell / PowerShell 7  
> **Current Milestone:** Phase 1 (Rust Engine) & Phase 2 (Python Core & IPC Client) **COMPLETE**  
> **Test Baseline:** 168 Python Tests (153 unit, 15 integration) | 79 Rust Tests | Latency P95: 0.36 ms  

This step-by-step guide is designed for you to test and verify everything built across **Phase 1** and **Phase 2** directly from your PowerShell terminal.

---

## 📋 Table of Contents

1. [Quick Verification (Run Everything)](#1-quick-verification-run-everything)
2. [Phase 2 — Python Core & IPC Client Testing](#2-phase-2--python-core--ipc-client-testing)
   - [2.1 All Python Unit Tests (153 tests)](#21-all-python-unit-tests-153-tests)
   - [2.2 Live End-to-End Integration Tests (15 tests)](#22-live-end-to-end-integration-tests-15-tests)
   - [2.3 IPC Roundtrip Latency Benchmark (100 requests)](#23-ipc-roundtrip-latency-benchmark-100-requests)
   - [2.4 Python Code Formatting & Linting](#24-python-code-formatting--linting)
3. [Phase 2 — Interactive Live Python Sandbox](#3-phase-2--interactive-live-python-sandbox)
   - [3.1 Querying Live Telemetry via `EngineClient`](#31-querying-live-telemetry-via-engineclient)
   - [3.2 Full Startup & Shutdown via `LifecycleManager`](#32-full-startup--shutdown-via-lifecyclemanager)
4. [Phase 1 — Rust Engine Verification (Compact)](#4-phase-1--rust-engine-verification-compact)
   - [4.1 Compiling & Running Rust Tests (79 tests)](#41-compiling--running-rust-tests-79-tests)
   - [4.2 Rust Formatting & Clippy Linter](#42-rust-formatting--clippy-linter)
   - [4.3 Running the Engine Manually](#43-running-the-engine-manually)
5. [Troubleshooting & Handy Tips](#5-troubleshooting--handy-tips)
6. [Master Command Cheat Sheet](#6-master-command-cheat-sheet)

---

## 1. Quick Verification (Run Everything)

From the project root (`C:\Users\vishnuu\Projects\TOM`), you can verify the entire test suite in three commands:

```powershell
# 1. Run all Python unit and integration tests (168 passed)
pytest tests/unit/ -v; pytest tests/integration/ -v

# 2. Run the IPC latency benchmark (< 10ms P95 target)
$env:PYTHONPATH="python"; python tests/integration/python_rust/bench_ipc.py

# 3. Check Python formatting and linting (0 errors)
ruff check python/ tests/; ruff format --check python/ tests/
```

To also verify the Rust engine in one command:
```powershell
cd rust\tom-engine; cargo test; cargo fmt --check; cargo clippy --all-targets --all-features -- -D warnings; cd ..\..
```

---

## 2. Phase 2 — Python Core & IPC Client Testing

All commands in this section should be executed from the **repository root**:
```powershell
cd C:\Users\vishnuu\Projects\TOM
```

### 2.1 All Python Unit Tests (153 tests)

Runs all unit tests across protocol schemas, exception taxonomy, named pipe transport, IPC client, request correlation, reconnection state machine, `EngineClient`, and `LifecycleManager`:

```powershell
pytest tests/unit/ -v
```

**Expected Result:**
```text
============================= 153 passed in ~1.0s =============================
```

#### Breakdown of Unit Test Coverage:
* `tests/unit/core/test_config.py` — Config schema validation & loading (5 tests)
* `tests/unit/ipc/test_protocol.py` — Protocol v1 wire format, ID generation, ErrorCode enum (20 tests)
* `tests/unit/ipc/test_errors.py` — Exception taxonomy and RemoteError predicates (20 tests)
* `tests/unit/ipc/test_transport.py` — Named pipe framing, 1MB limit, timeout, mock transport (24 tests)
* `tests/unit/ipc/test_client.py` — Correlation, receive loop, timeouts, cancellation, reconnection backoff (32 tests)
* `tests/unit/ipc/test_engine_client.py` — Typed domain models, hardware fallback, method routing (36 tests)
* `tests/unit/ipc/test_lifecycle.py` — Startup sequence, idempotent stop, signal handling (12 tests)
* `tests/unit/telemetry/test_logging.py` — Secret redactor & structured JSON logging (4 tests)

---

### 2.2 Live End-to-End Integration Tests (15 tests)

Runs live end-to-end integration tests over the real Windows Named Pipe (`\\.\pipe\tom-engine`). The test suite **automatically starts and stops `tom-engine.exe`** if it is not already running:

```powershell
pytest tests/integration/ -v
```

**Expected Result:**
```text
tests/integration/python_rust/test_lifecycle.py ..                       [ 13%]
tests/integration/python_rust/test_live_ipc.py .............             [100%]
============================= 15 passed in ~0.6s ==============================
```

#### What is Verified Live:
1. `engine.ping` → Validates roundtrip pong and engine version
2. `engine.status` → Confirms engine is "running"
3. `system.cpu` → Live core count and CPU utilization percentage
4. `system.memory` → Real RAM total/used/available bytes
5. `system.gpu` → GPU telemetry or graceful fallback if absent
6. `system.battery` → Battery charge state or desktop fallback
7. `system.disk` → Mounted drive volumes and storage metrics
8. `system.processes` → Top processes ranked by resource consumption
9. `system.all` → Aggregated snapshot of all 6 telemetry domains
10. **10 concurrent pings** → High-concurrency correlation without crosstalk
11. **Remote errors** → `NOT_FOUND` on unknown methods and `VERSION_MISMATCH` on bad versions
12. **Zero task leaks** → Active task audit across 100 sequential requests
13. **Lifecycle 2-cycle test** → Full startup and shutdown executed twice without leaks

---

### 2.3 IPC Roundtrip Latency Benchmark (100 requests)

Measures 100 sequential requests between Python and Rust across the Windows Named Pipe:

```powershell
$env:PYTHONPATH="python"
python tests/integration/python_rust/bench_ipc.py
```

**Expected Output:**
```text
==================================================
      TOM IPC Roundtrip Latency Benchmark      
==================================================
Iterations:     100
Min:            0.109 ms
Mean:           0.172 ms
P50 (Median):   0.144 ms
P95:            0.359 ms (Target: < 10.0 ms)
P99:            0.448 ms
Max:            0.448 ms
==================================================
RESULT: PASS (P95 0.359 ms < 10.0 ms target)
```

---

### 2.4 Python Code Formatting & Linting

Verify that all Python code complies with Ruff rules:

```powershell
# Run the Ruff linter
ruff check python/ tests/

# Verify formatting without altering files
ruff format --check python/ tests/
```

**Expected Result:** `All checks passed!` with 0 violations and 0 diffs.

---

## 3. Phase 2 — Interactive Live Python Sandbox

Try communicating with the Rust engine interactively using the high-level Python APIs.

### 3.1 Querying Live Telemetry via `EngineClient`

Run this PowerShell command to connect to `tom-engine`, query live CPU, RAM, and Battery stats, and print them:

```powershell
$env:PYTHONPATH="python"
python -c "
import asyncio
from tom.ipc.client import NamedPipeIpcClient
from tom.core.engine import EngineClient

async def main():
    client = NamedPipeIpcClient()
    engine = EngineClient(client)
    await client.connect()
    try:
        ping = await engine.ping()
        cpu = await engine.get_cpu()
        mem = await engine.get_memory()
        print(f'Engine Connected: {ping.version}')
        print(f'CPU: {cpu.core_count} cores | Usage: {cpu.usage_percent:.1f}%')
        print(f'RAM: {mem.used_bytes / (1024**3):.2f} GB used / {mem.total_bytes / (1024**3):.2f} GB total')
    finally:
        await client.close()

asyncio.run(main())
"
```

> **Note:** If `tom-engine` is not already running in the background, run `cargo run` in another terminal or run the `LifecycleManager` snippet below!

---

### 3.2 Full Startup & Shutdown via `LifecycleManager`

Test the complete lifecycle startup and shutdown coordinator:

```powershell
$env:PYTHONPATH="python"
python -c "
import asyncio
from tom.core.lifecycle import LifecycleManager

async def demo():
    manager = LifecycleManager()
    print('Starting TOM Lifecycle...')
    await manager.start()
    print(f'TOM is running: {manager.is_running}')
    
    # Query via manager.engine
    status = await manager.engine.status()
    print(f'Engine health: {status.status}')
    
    print('Shutting down TOM cleanly...')
    await manager.stop()
    print(f'TOM is stopped: {manager.is_stopped}')

asyncio.run(demo())
"
```

---

## 4. Phase 1 — Rust Engine Verification (Compact)

All commands in this section should be executed from `rust/tom-engine`:
```powershell
cd C:\Users\vishnuu\Projects\TOM\rust\tom-engine
```

### 4.1 Compiling & Running Rust Tests (79 tests)

```powershell
# Run all unit tests (72 tests) + integration hardening tests (7 tests)
cargo test
```
**Expected Result:** `test result: ok. 72 passed` and `test result: ok. 7 passed` (total 79 passed).

To see live latency measurements and timing logs during the integration hardening test:
```powershell
cargo test --test integration_hardening -- --nocapture
```

### 4.2 Rust Formatting & Clippy Linter

```powershell
# Check formatting
cargo fmt --check

# Strict Clippy lint check (zero warnings)
cargo clippy --all-targets --all-features -- -D warnings
```

### 4.3 Running the Engine Manually

To run the engine manually in a dedicated terminal:
```powershell
cargo run
```
You will see:
```text
{"timestamp":"...","level":"INFO","fields":{"message":"Starting TOM Engine","engine":"tom-engine","version":"0.1.0"}}
{"timestamp":"...","level":"INFO","fields":{"message":"TOM Engine initialized. Awaiting signals or shutdown..."}}
```
Press **`Ctrl + C`** to gracefully shut down the engine.

---

## 5. Troubleshooting & Handy Tips

### Issue: Named pipe not found or connection timeout
* **Cause:** `tom-engine` is not running.
* **Solution:** 
  * If running integration tests (`pytest tests/integration/ -v`) or the benchmark (`bench_ipc.py`), make sure you ran `cargo build` in `rust/tom-engine` so the binary `target/debug/tom-engine.exe` exists. The integration test suite will automatically launch it!
  * Or manually start the engine in another terminal: `cd rust\tom-engine; cargo run`.

### Issue: "Access denied" or "pipe busy"
* **Cause:** A stale background `tom-engine` instance is holding the pipe.
* **Solution:** Kill any running instances in PowerShell:
  ```powershell
  Stop-Process -Name "tom-engine" -Force -ErrorAction SilentlyContinue
  ```

### Issue: Python `ModuleNotFoundError: No module named 'tom'`
* **Cause:** `PYTHONPATH` does not include `python`.
* **Solution:** Set the environment variable in PowerShell:
  ```powershell
  $env:PYTHONPATH="python"
  ```

---

## 6. Master Command Cheat Sheet

| Category | Goal | PowerShell Command |
| :--- | :--- | :--- |
| **Python** | Run all unit tests | `pytest tests/unit/ -v` |
| **Python** | Run live integration tests | `pytest tests/integration/ -v` |
| **Python** | Run all tests (unit + integration) | `pytest tests/ -v` |
| **Python** | Run IPC latency benchmark | `$env:PYTHONPATH="python"; python tests/integration/python_rust/bench_ipc.py` |
| **Python** | Check linting | `ruff check python/ tests/` |
| **Python** | Check formatting | `ruff format --check python/ tests/` |
| **Python** | Auto-fix formatting | `ruff format python/ tests/` |
| **Rust** | Run all tests (unit + integration) | `cd rust\tom-engine; cargo test; cd ..\..` |
| **Rust** | Run integration benchmarks | `cd rust\tom-engine; cargo test --test integration_hardening -- --nocapture; cd ..\..` |
| **Rust** | Strict Clippy linter | `cd rust\tom-engine; cargo clippy --all-targets --all-features -- -D warnings; cd ..\..` |
| **Rust** | Check formatting | `cd rust\tom-engine; cargo fmt --check; cd ..\..` |
| **Rust** | Launch engine manually | `cd rust\tom-engine; cargo run` |
| **Cleanup** | Kill running `tom-engine` processes | `Stop-Process -Name "tom-engine" -Force -ErrorAction SilentlyContinue` |

---

🎉 **Phase 1 and Phase 2 are 100% complete and verified.** The repository is fully prepared for **Phase 3 — Deterministic Tools & Permission Engine**.
