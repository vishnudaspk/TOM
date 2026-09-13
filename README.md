# TOM

> A local, modular, privacy-oriented personal AI assistant and operating layer.

---

## Current Status

| Milestone | Subsystem / Focus | Status | Tests |
|---|---|---|---|
| **Phase 0** | Project Bootstrap, Configuration & Structured Logging | **COMPLETE** | 9 passed |
| **Phase 1** | Rust Engine (`tom-engine`) — Lifecycle, Telemetry & IPC Server | **COMPLETE** | 79 passed |
| **Phase 2** | Python Core & IPC Client (`NamedPipeIpcClient`, `EngineClient`, `LifecycleManager`) | **COMPLETE** | 168 passed |
| **Phase 3** | Deterministic Tools & Permission Engine (`registry`, `permissions`, `executor`) | **PLANNED / READY** | Next |

* **Current Next Task**: Phase 3, Iteration 1 — Tool Definition, Base Models & Tool Registry (`python/tom/tools/registry.py`).
* **Verified Test Baseline**: **247 Total Tests Passing** (168 Python, 79 Rust).
* **IPC Roundtrip Latency**: P50 = 0.144 ms, P95 = 0.359 ms (Target: < 10.0 ms).
* **Quality Gates**: Ruff clean (0 violations, 0 diffs), Cargo clippy clean (0 warnings), Cargo fmt clean (0 diffs).

---

## Architecture

TOM uses a dual-core design: **Python Brain** for high-level orchestration, tools, memory, and future agents, paired with **Rust Engine (`tom-engine`)** for deterministic, low-level OS operations, audio primitives, hardware telemetry, and Windows Named Pipe IPC.

```text
                        TOM Python Core
                               │
                        LifecycleManager
                               │
                         EngineClient
                               │
                      NamedPipeIpcClient
                               │
                      NamedPipeTransport
                               │
              Windows Named Pipe \\.\pipe\tom-engine
                               │
                       Rust tom-engine
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
     CPU / RAM            GPU / Battery        Disk / Processes
     telemetry              telemetry             telemetry
```

---

## Phase 3 Direction

Phase 3 establishes the deterministic tool execution and security foundation:
* **Tool Registry**: Discovery, cataloging, `@tool` decorator, and automated Pydantic v2 schema generation.
* **Permission Engine**: Centralized 3-tier pre-execution policy evaluation (`SAFE`, `ASK USER`, `BLOCK`).
* **Tool Executor**: Pre-execution security gating, argument validation, timeout deadlines, and cooperative cancellation.
* **Deterministic System Tools**: Hardware and OS inspection wrapping Phase 2's `EngineClient`.
* **Sandboxed File Tools**: Whitelisted directory access with path-traversal (`../`) guards.
* **End-to-End Tool Pipeline**: Sub-millisecond tool execution (< 1.0 ms dispatch overhead).

---

## Development Environment

* **Operating System**: Windows 11 (MSVC toolchain)
* **Python**: 3.11.9 (in dedicated virtual environment)
* **Rust**: 1.80+ (Tokio 1.43, Serde, Tracing)
* **Testing & Tooling**: pytest 9.1.1, Ruff 0.16.7, Cargo

### Python Environment Setup

All TOM Python packages, tools, and tests **must** run within the repository's dedicated `.venv`. Never install packages globally or into unrelated environments.

```powershell
# Create virtual environment (if not present)
python -m venv .venv

# Verify active interpreter resolves to .venv
.\.venv\Scripts\python.exe -c "import sys; print(sys.executable)"

# Install dependencies into .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

---

## Testing & Quality Gates

Run tests and linters directly from PowerShell using `.venv`:

```powershell
# Python Unit Tests (153 tests)
.\.venv\Scripts\pytest.exe tests/unit/ -v

# Python Live Integration Tests against tom-engine (15 tests)
.\.venv\Scripts\pytest.exe tests/integration/ -v

# Python Linting & Formatting
.\.venv\Scripts\ruff.exe check python/ tests/
.\.venv\Scripts\ruff.exe format --check python/ tests/

# IPC Roundtrip Latency Benchmark (100 requests)
$env:PYTHONPATH="python"; .\.venv\Scripts\python.exe tests/integration/python_rust/bench_ipc.py

# Rust Tests & Clippy (from rust/tom-engine)
cd rust\tom-engine; cargo test; cargo clippy --all-targets --all-features -- -D warnings; cd ..\..
```

---

## Project Structure

```text
TOM/
├── .venv/                      # Dedicated Python virtual environment
├── config/                     # Default YAML configuration
├── python/                     # Python Brain (orchestration, IPC, lifecycle)
│   └── tom/
│       ├── core/               # EngineClient, LifecycleManager, config loader
│       ├── ipc/                # NamedPipeIpcClient, transport, protocol, errors
│       ├── schemas/            # Pydantic v2 configuration models
│       ├── security/           # PermissionEngine, ConfirmationHook (Phase 3)
│       ├── telemetry/          # Structured JSON logging & secret redactor
│       └── tools/              # ToolRegistry, ToolExecutor, system & file tools (Phase 3)
├── rust/                       # Rust Engine (low-level OS layer)
│   └── tom-engine/             # Tokio async runtime, Named Pipe server, hardware monitors
├── tests/
│   ├── unit/                   # Isolated unit tests (Mock transport, no Rust needed)
│   └── integration/            # Live end-to-end integration tests (Windows pipe)
├── pyproject.toml              # Python project metadata & tool configs
├── IMPLEMENTATIONPLAN.md       # Master 15-phase implementation roadmap
├── PHASE2_IMPLEMENTATIONPLAN.md# Phase 2 specification (Complete)
├── PHASE3_IMPLEMENTATIONPLAN.md# Phase 3 detailed specification (Approved)
├── STATE.md                    # Current state snapshot & architectural decisions
├── PROGRESS.md                 # Chronological development ledger
└── HANDOFF.md                  # Next-session developer handoff
```

---

## Development Principles

1. **Python / Rust Dual-Core Architecture**: Python manages intelligence, orchestration, tools, and memory; Rust handles low-level OS operations, audio, sensors, and IPC.
2. **Strict `.venv` Isolation**: All package installations and executions are restricted to `.venv`.
3. **Deterministic Tools**: Tools are typed, bounded, and callable directly by Python code.
4. **Centralized Pre-Execution Permissions**: Policy checks (`SAFE`, `ASK USER`, `BLOCK`) occur before execution; tools never evaluate their own safety.
5. **LLM/Agent Independence**: Tools must never import or depend on LLM models, prompts, routers, or agent frameworks. Future models are consumers of tools.
6. **Security-First Design**: Path-traversal protection, secret redaction in logs, bounded timeouts, and zero arbitrary shell execution.
7. **Incremental Verification**: Strict iteration gates with 100% test pass baselines and clean quality checks before advancing.

---

## Documentation Quick Links

* [Master Implementation Plan](file:///c:/Users/vishnuu/Projects/TOM/IMPLEMENTATIONPLAN.md) — 15-phase comprehensive roadmap
* [Phase 3 Specification](file:///c:/Users/vishnuu/Projects/TOM/PHASE3_IMPLEMENTATIONPLAN.md) — Approved iteration breakdown for Tools & Permissions
* [State Snapshot](file:///c:/Users/vishnuu/Projects/TOM/STATE.md) — Current state, active decisions, and baseline metrics
* [Progress Ledger](file:///c:/Users/vishnuu/Projects/TOM/PROGRESS.md) — Complete history of all completed iterations
* [Developer Handoff](file:///c:/Users/vishnuu/Projects/TOM/HANDOFF.md) — Unambiguous handoff for the next development session
* [Testing Guide](file:///c:/Users/vishnuu/Projects/TOM/test.md) — Hands-on testing & verification walkthrough
