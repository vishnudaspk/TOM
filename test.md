# TOM — Testing & Verification Guide

> **OS:** Windows 11 | **Shell:** PowerShell | **Python:** 3.11 | **Phases Complete:** 1, 2, 3, 4 (Iterations 1, 2 & 3)

This guide walks you through verifying everything built so far — from the Rust engine all the way up to the AI Agent and Model Provider layers.
You don't need to know Rust or be a Python expert. Just follow each step in order.

---

## 📋 Table of Contents

1. [Before You Start — One-Time Setup](#1-before-you-start--one-time-setup)
2. [Quick Check — Run Everything at Once](#2-quick-check--run-everything-at-once)
3. [Phase 1 — Rust Engine Tests](#3-phase-1--rust-engine-tests)
4. [Phase 2 — Python Core & IPC Tests](#4-phase-2--python-core--ipc-tests)
5. [Phase 3 — Tools & Security Tests](#5-phase-3--tools--security-tests)
6. [Phase 4 — Agent Framework & Model Provider Tests](#6-phase-4--agent-framework--model-provider-tests)
7. [Live Interactive Demos](#7-live-interactive-demos)
8. [Troubleshooting](#8-troubleshooting)
9. [Command Cheat Sheet](#9-command-cheat-sheet)

---

## 1. Before You Start — One-Time Setup

Every command in this guide must be run from the **project root folder**.

Open PowerShell and navigate there first:

```powershell
cd C:\Users\vishnuu\Projects\TOM
```

> 💡 **You only need to do this once per terminal session.** All subsequent commands assume you're in this folder.

---

## 2. Quick Check — Run Everything at Once

Want to verify the whole project in one go? Run these three commands:

```powershell
# Step 1 — Run ALL Python tests (unit + integration) — expect 469 passed
.\.venv\Scripts\pytest.exe tests/ -q --tb=short

# Step 2 — Check Python code quality (expect "All checks passed!")
.\.venv\Scripts\ruff.exe check python/ tests/
.\.venv\Scripts\ruff.exe format --check python/ tests/

# Step 3 — Run Rust engine tests (expect 79 passed)
cd rust\tom-engine
cargo test
cd ..\..
```

**What to expect:**
| Check | Expected Result |
|:---|:---|
| Python tests | `469 passed` (419 unit + 50 integration) |
| Ruff lint | `All checks passed!` |
| Ruff format | `69 files already formatted` |
| Rust tests | `79 passed` |
| **Total Verified** | **548 passed** |

---

## 3. Phase 1 — Rust Engine Tests

The Rust engine (`tom-engine`) is the always-on background process that handles system telemetry, audio, and IPC.

### 3.1 Run Rust Unit & Integration Tests

```powershell
cd rust\tom-engine
cargo test
```

**Expected output:**
```
test result: ok. 72 passed  ← unit tests
test result: ok. 7 passed   ← integration tests
```

### 3.2 Check Rust Code Quality

```powershell
# Check formatting (no output = clean)
cargo fmt --check

# Strict linter (no warnings allowed)
cargo clippy --all-targets --all-features -- -D warnings
```

### 3.3 Start the Engine Manually (optional)

If you want to see the engine running live:

```powershell
cargo run
```

You'll see JSON log lines like:
```json
{"level":"INFO","message":"Starting TOM Engine","version":"0.1.0"}
{"level":"INFO","message":"TOM Engine initialized. Awaiting signals or shutdown..."}
```

Press **Ctrl + C** to stop it cleanly.

```powershell
# Go back to project root when done
cd ..\..
```

---

## 4. Phase 2 — Python Core & IPC Tests

Phase 2 is the Python side: it connects to the Rust engine over a Windows Named Pipe and provides a clean async API.

### 4.1 Run Python Unit Tests

```powershell
.\.venv\Scripts\pytest.exe tests/unit/ -v
```

**Expected result:** `419 passed`

The unit tests cover:
- Config schema loading
- IPC protocol wire format
- Named pipe transport
- Request/response correlation
- Reconnection state machine
- `EngineClient` typed API
- `LifecycleManager` startup/shutdown
- Tools, security, agent, and model provider layers

### 4.2 Run Live Integration Tests

These tests **automatically start and stop `tom-engine.exe`** — you don't need to do anything extra.

```powershell
.\.venv\Scripts\pytest.exe tests/integration/ -v
```

**Expected result:** `50 passed` (47 engine/tool integration + 3 live LM Studio smoke tests)

These tests verify:
- Live CPU, RAM, GPU, battery, disk, process telemetry over Named Pipe
- 10 concurrent requests without errors
- Latency well under 10ms
- Lifecycle startup and shutdown
- End-to-end tool execution and permission checks
- Live LM Studio generation and streaming (if LM Studio is running)

### 4.3 Run the IPC Latency Benchmark

This measures how fast Python and Rust can talk to each other (target: under 10ms P95):

```powershell
$env:PYTHONPATH = "python"
python tests/integration/python_rust/bench_ipc.py
```

**Expected output:**
```
P95:    0.359 ms  (Target: < 10.0 ms)
RESULT: PASS
```

---

## 5. Phase 3 — Tools & Security Tests

Phase 3 built a deterministic tool execution engine with a 3-tier security model (SAFE / ASK_USER / BLOCK).

### 5.1 Run Tool & Security Unit Tests

```powershell
.\.venv\Scripts\pytest.exe tests/unit/tools/ tests/unit/security/ -v
```

**Expected result:** `127 passed`

Covers:
- `ToolRegistry` — tool registration and lookup
- `ToolDefinition` — schema extraction and JSON export
- `PermissionEngine` — SAFE/ASK_USER/BLOCK policy enforcement
- `ConfirmationHook` — user confirmation flows
- `ToolExecutor` — full pipeline: lookup → permission → confirm → validate → run → result
- System tools (CPU, RAM, GPU, battery, disk, processes)
- File tools (read, list, search, write, delete) with `PathGuard` sandbox

### 5.2 Run Tool Integration Tests

```powershell
.\.venv\Scripts\pytest.exe tests/integration/tools/ -v
```

**Expected result:** `47 passed`

These are end-to-end tests through the full tool pipeline including real file operations and live system queries.

### 5.3 Try a Tool Manually

Run a live CPU info query through the tool system:

```powershell
$env:PYTHONPATH = "python"
python -c "
import asyncio
from tom.tools.bootstrap import setup_default_tools
from tom.tools.registry import ToolRegistry
from tom.tools.executor import ToolExecutor

async def main():
    reg = ToolRegistry()
    setup_default_tools(registry=reg)
    executor = ToolExecutor(registry=reg)
    result = await executor.execute('system.cpu_info')
    print('Success:', result.success)
    print('CPU cores:', result.data.core_count)
    print('CPU usage:', result.data.usage_percent, '%')

asyncio.run(main())
"
```

---

## 6. Phase 4 — Agent Framework & Model Provider Tests

Phase 4 implements the AI Agent layer and the Model Provider boundary:

1. **Stateful Agent** with a strict 6-state lifecycle (`IDLE`, `THINKING`, `ACTING`, `WAITING_CONFIRMATION`, `ERROR`, `TERMINATED`).
2. **Centralized Tool Execution** through `ToolExecutor` with automatic confirmation handling.
3. **Pluggable Model Provider** interface (`LLMProvider` / `ModelProvider`), supporting deterministic mock testing and local LM Studio / Bionic endpoints.

### Agent States (the lifecycle)

```
IDLE → THINKING → ACTING → WAITING_CONFIRMATION → ACTING → THINKING
                         ↘ ERROR / TERMINATED
```

### 6.1 Run All Agent Unit Tests

```powershell
.\.venv\Scripts\pytest.exe tests/unit/agents/ -v
```

**Expected result:** `78 passed`

Covers:
- **State machine** — valid and invalid transitions, callbacks, `StateChangeEvent`
- **Conversation history** — bounded context, system prompt anchoring, trimming
- **Tool execution** — `execute_tool()` with SAFE, ASK_USER (approve & deny), BLOCK, errors, and cancellation

### 6.2 Run Model Provider Unit Tests

```powershell
.\.venv\Scripts\pytest.exe tests/unit/models/ -v
```

**Expected result:** `61 passed`

Covers:
- `ModelRequest`, `ModelResponse`, `StreamChunk`, `TokenUsage` schema validation
- `MockModelProvider` scripted responses, error injection, simulated streaming
- `HttpModelProvider` and `LMStudioProvider` OpenAI-compatible request building and SSE streaming
- `AgentDependencies` model provider injection (agent decoupled from concrete providers)
- Clean error hierarchy (`ModelConnectionError`, `ModelAPIError`, `ModelTimeoutError`, `ModelResponseError`)

### 6.3 Run Live LM Studio Smoke Test

If you have LM Studio running locally on `http://localhost:1234/v1`:

```powershell
.\.venv\Scripts\pytest.exe tests/integration/models/ -v
```

**Expected result:** `3 passed` (auto-detects loaded model like `qwen3-8b`, tests health, generation, and streaming).
*(Note: If LM Studio is not running, these tests skip automatically without failing).*

### 6.4 Try the Agent Manually

Create an agent, run a tool, and inspect the conversation history:

```powershell
$env:PYTHONPATH = "python"
python -c "
import asyncio
from tom.agents.base import Agent
from tom.agents.dependencies import AgentDependencies
from tom.tools.bootstrap import setup_default_tools
from tom.tools.registry import ToolRegistry

async def main():
    reg = ToolRegistry()
    setup_default_tools(registry=reg)
    deps = AgentDependencies(registry=reg)
    agent = Agent(dependencies=deps)

    print('Agent state:', agent.state.value)
    result = await agent.execute_tool('system.cpu_info')
    print('Tool succeeded:', result.success)
    print('Agent state after:', agent.state.value)
    print('Messages in history:', len(agent.history))

asyncio.run(main())
"
```

### 6.5 Try the Model Provider Manually

#### Option A: Using Mock Provider (Zero Network, 100% Deterministic)

```powershell
$env:PYTHONPATH = "python"
python -c "
import asyncio
from tom.models.providers.mock import MockModelProvider
from tom.schemas.agent import Message, Role
from tom.schemas.models import ModelRequest

async def main():
    provider = MockModelProvider(responses=['Hello from TOM mock brain!'])
    req = ModelRequest(
        model='test-model',
        messages=[Message(role=Role.USER, content='Hi TOM!')]
    )
    resp = await provider.generate(req)
    print('Response:', resp.content)

asyncio.run(main())
"
```

#### Option B: Using Live LM Studio (Requires LM Studio running at `http://localhost:1234/v1`)

```powershell
$env:PYTHONPATH = "python"
python -c "
import asyncio
from tom.models.lmstudio import LMStudioProvider
from tom.schemas.agent import Message, Role
from tom.schemas.models import ModelRequest

async def main():
    provider = LMStudioProvider(base_url='http://localhost:1234/v1')
    healthy = await provider.check_health()
    print('LM Studio Healthy:', healthy)
    if healthy:
        req = ModelRequest(
            model='qwen3-8b',
            messages=[Message(role=Role.USER, content='Reply with one word: TOM')]
        )
        resp = await provider.generate(req)
        print('Generated:', resp.content or resp.reasoning_content)

asyncio.run(main())
"
```

---

## 7. Live Interactive Demos

### Demo 1 — Query System Telemetry via EngineClient

Connect to the Rust engine and fetch live system stats:

```powershell
$env:PYTHONPATH = "python"
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
        cpu  = await engine.get_cpu()
        mem  = await engine.get_memory()
        print(f'Engine version : {ping.version}')
        print(f'CPU cores      : {cpu.core_count}')
        print(f'CPU usage      : {cpu.usage_percent:.1f}%')
        print(f'RAM used       : {mem.used_bytes / 1024**3:.2f} GB / {mem.total_bytes / 1024**3:.2f} GB')
    finally:
        await client.close()

asyncio.run(main())
"
```

> **Note:** `tom-engine` must be running for this demo (`cargo run` in `rust\tom-engine`).

### Demo 2 — Full Lifecycle Startup & Shutdown

```powershell
$env:PYTHONPATH = "python"
python -c "
import asyncio
from tom.core.lifecycle import LifecycleManager

async def demo():
    manager = LifecycleManager()
    print('Starting TOM...')
    await manager.start()
    print('Running:', manager.is_running)
    status = await manager.engine.status()
    print('Engine status:', status.status)
    print('Shutting down...')
    await manager.stop()
    print('Stopped:', manager.is_stopped)

asyncio.run(demo())
"
```

---

## 8. Troubleshooting

### ❌ `No module named 'tom'`

The Python path isn't set. Fix:
```powershell
$env:PYTHONPATH = "python"
```

### ❌ Named pipe not found / connection timeout

The `tom-engine` binary isn't running.
- The **integration tests** launch it automatically — no action needed.
- For **manual demos**, start it yourself in a separate terminal:
  ```powershell
  cd C:\Users\vishnuu\Projects\TOM\rust\tom-engine
  cargo run
  ```

### ❌ "Access denied" or "pipe busy"

A stale engine process is blocking the pipe:
```powershell
Stop-Process -Name "tom-engine" -Force -ErrorAction SilentlyContinue
```

### ❌ LM Studio connection refused

LM Studio server is not started on port 1234.
- Open LM Studio -> Developer / Local Server tab -> click **Start Server**.
- Ensure endpoint is `http://localhost:1234/v1`.

---

## 9. Command Cheat Sheet

Run all commands from `C:\Users\vishnuu\Projects\TOM`.

| What | Command |
|:---|:---|
| **Run ALL Python tests** | `.\.venv\Scripts\pytest.exe tests/ -q` |
| **Run unit tests only** | `.\.venv\Scripts\pytest.exe tests/unit/ -v` |
| **Run integration tests only** | `.\.venv\Scripts\pytest.exe tests/integration/ -v` |
| **Run agent tests only** | `.\.venv\Scripts\pytest.exe tests/unit/agents/ -v` |
| **Run model tests only** | `.\.venv\Scripts\pytest.exe tests/unit/models/ -v` |
| **Run live LM Studio smoke test** | `.\.venv\Scripts\pytest.exe tests/integration/models/ -v` |
| **Run tool tests only** | `.\.venv\Scripts\pytest.exe tests/unit/tools/ -v` |
| **Run security tests only** | `.\.venv\Scripts\pytest.exe tests/unit/security/ -v` |
| **IPC latency benchmark** | `$env:PYTHONPATH="python"; python tests/integration/python_rust/bench_ipc.py` |
| **Lint check** | `.\.venv\Scripts\ruff.exe check python/ tests/` |
| **Format check** | `.\.venv\Scripts\ruff.exe format --check python/ tests/` |
| **Auto-fix formatting** | `.\.venv\Scripts\ruff.exe format python/ tests/` |
| **Run Rust tests** | `cd rust\tom-engine; cargo test; cd ..\..` |
| **Rust clippy** | `cd rust\tom-engine; cargo clippy --all-targets --all-features -- -D warnings; cd ..\..` |
| **Rust format check** | `cd rust\tom-engine; cargo fmt --check; cd ..\..` |
| **Start engine manually** | `cd rust\tom-engine; cargo run` |
| **Kill engine process** | `Stop-Process -Name "tom-engine" -Force -ErrorAction SilentlyContinue` |

---

## Current Test Baseline

| Layer | Tests | Status |
|:---|:---:|:---:|
| Rust Engine (`cargo test`) | **79** | ✅ |
| Python Unit (`tests/unit/`) | **419** | ✅ |
| Python Integration (`tests/integration/`) | **50** | ✅ |
| **Total** | **548** | ✅ |

**Phases complete: 0 → 1 → 2 → 3 → 4 (Iterations 1, 2 & 3)**
**Next up: Phase 4, Iteration 4 — Two-Tier Intent & Model Router**
