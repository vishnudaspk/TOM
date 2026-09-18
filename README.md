# TOM

> A local, modular, privacy-oriented personal AI assistant and operating layer.

---

## Current Status

| Milestone   | Subsystem / Focus                                                                   | Status                                         |          Tests |
| ----------- | ----------------------------------------------------------------------------------- | ---------------------------------------------- | -------------: |
| **Phase 0** | Project Bootstrap, Configuration & Structured Logging                               | **COMPLETE**                                   |              9 |
| **Phase 1** | Rust Engine (`tom-engine`) — Lifecycle, Telemetry & IPC Server                      | **COMPLETE**                                   |             79 |
| **Phase 2** | Python Core & IPC Client — `NamedPipeIpcClient`, `EngineClient`, `LifecycleManager` | **COMPLETE**                                   |            168 |
| **Phase 3** | Deterministic Tools & Permission Engine                                             | **COMPLETE**                                   |            406 |
| **Phase 4** | Agent Framework & Local Model Routing                                               | **IMPLEMENTATION COMPLETE — CLOSEOUT PENDING** | 621 baseline\* |

- Current verified repository baseline before final Phase 4 closeout documentation changes: **492 Python unit + 50 Python integration + 79 Rust = 621 tests passing**.

### Current Development State

**Phase 4 — Agent Framework & Local Model Routing**

The Phase 4 implementation is complete through Iteration 6's integration and exit-gate verification. Final documentation cleanup and phase-plan deletion remain as the administrative closeout step.

Phase 5 has **not** started.

### Phase 4 Capabilities

- **Agent State Machine** — Explicit deterministic lifecycle and validated state transitions.
- **Agent Tool Integration** — Agents execute tools exclusively through the centralized `ToolExecutor`.
- **Model Provider Boundary** — Provider-independent `LLMProvider` / `ModelProvider` abstraction.
- **LM Studio Provider** — OpenAI-compatible local model integration, including streaming support.
- **Two-Tier Intent Router** — Fast deterministic routing with model-backed fallback.
- **Multi-Step Agent Loop** — Bounded reasoning/action loop with `max_steps` protection.
- **Cooperative Cancellation** — Cancellation propagation through agent execution.
- **Error Recovery** — Controlled model/tool failure handling without uncontrolled execution.
- **End-to-End Pipeline** — User prompt → routing → tool/agent execution → response.

---

## Architecture

TOM uses a dual-core architecture:

- **Python Brain** — high-level orchestration, agents, model routing, tools, memory, and future intelligence.
- **Rust Engine (`tom-engine`)** — deterministic low-level OS operations, hardware/audio primitives, telemetry, global system integration, and IPC.

```text
                         TOM Python Brain
                                │
                    ┌───────────┴───────────┐
                    │                       │
              IntentRouter             Agent Layer
                    │                       │
                    │                AgentOrchestrator
                    │                       │
                    └───────────┬───────────┘
                                │
                         ToolExecutor
                                │
                       PermissionEngine
                                │
                    ┌───────────┴───────────┐
                    │                       │
               System Tools            File Tools
                    │                       │
                    └───────────┬───────────┘
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
              ┌─────────────────┼─────────────────┐
              │                 │                 │
          CPU / RAM         GPU / Battery    Disk / Processes
           telemetry          telemetry         telemetry
```

---

## Model & Agent Architecture

Phase 4 establishes the model-independent agent boundary used by TOM.

```text
                         User Prompt
                              │
                              ▼
                       ┌─────────────┐
                       │ IntentRouter│
                       └──────┬──────┘
                              │
                    ┌─────────┴─────────┐
                    │                   │
              Direct Tool          Agent Loop
                    │                   │
                    │             ModelProvider
                    │                   │
                    │             Tool Call Request
                    │                   │
                    └─────────┬─────────┘
                              ▼
                       ToolExecutor
                              │
                    PermissionEngine
                              │
                         Tool Result
                              │
                              ▼
                        Final Response
```

### Two-Tier Intent Routing

**Tier 1 — Deterministic**

Fast heuristic classification for obvious intents such as system, file, and direct tool operations.

**Tier 2 — Model-backed**

Uses the configured `LLMProvider` when deterministic routing cannot confidently classify the request.

Provider failures and malformed routing responses have deterministic fallback behavior rather than uncontrolled execution.

---

## Phase 3 — Deterministic Tool & Security Foundation

Phase 3 established TOM's tool execution boundary:

- **Tool Registry** — Typed tool discovery and registration.
- **Pydantic v2 Schemas** — Structured tool arguments and validation.
- **Permission Engine** — Centralized `SAFE`, `ASK_USER`, and `BLOCK` policy evaluation.
- **Tool Executor** — Validation, permission gating, bounded execution, timeout handling, and cancellation.
- **System Tools** — Deterministic OS/hardware operations through the Rust engine.
- **File Tools** — Sandboxed filesystem access with path-traversal protection.
- **No Arbitrary Shell Execution** — Tools operate through explicitly defined interfaces.

All Phase 4 agents consume this existing tool boundary rather than bypassing it.

---

## Development Environment

- **Operating System:** Windows 11
- **Toolchain:** MSVC
- **Python:** 3.11.9
- **Pydantic:** v2
- **Rust:** 1.80+
- **Async Runtime:** Tokio 1.43
- **Testing:** pytest 9.1.1
- **Linting / Formatting:** Ruff 0.16.7
- **IPC:** Windows Named Pipes
- **Python Environment:** Dedicated repository `.venv`

### Python Environment Setup

All TOM Python packages, tools, and tests **must** run inside the repository's dedicated `.venv`.

Never install project dependencies globally or into an unrelated Python environment.

```powershell
# Create virtual environment if not present
python -m venv .venv

# Verify the interpreter
.\.venv\Scripts\python.exe -c "import sys; print(sys.executable)"

# Install TOM
.\.venv\Scripts\python.exe -m pip install -e .
```

---

## Testing & Quality Gates

Run Python tests and tooling through the dedicated `.venv`.

```powershell
# Python unit tests
.\.venv\Scripts\pytest.exe tests/unit/ -v

# Python integration tests
.\.venv\Scripts\pytest.exe tests/integration/ -v

# Ruff
.\.venv\Scripts\ruff.exe check python/ tests/

# Formatting check
.\.venv\Scripts\ruff.exe format --check python/ tests/

# Rust tests
cd rust\tom-engine
cargo test

# Rust formatting
cargo fmt --check

# Rust linting
cargo clippy --all-targets --all-features -- -D warnings

# Return to repository root
cd ..\..
```

### Verified Phase 4 Baseline

The latest completed Phase 4 implementation/exit-gate run verified:

```text
Python unit tests:        492 passed
Python integration tests:  50 passed
Rust tests:                79 passed
────────────────────────────────────
Total:                    621 passed
```

Quality gates:

```text
Ruff check:        CLEAN
Ruff format:       CLEAN
Cargo fmt:         CLEAN
Cargo clippy:      CLEAN
```

### IPC Performance Baseline

Previously measured Rust/Python IPC roundtrip performance:

```text
P50:  0.144 ms
P95:  0.359 ms
Target: < 10 ms
```

Phase 4 routing/dispatch benchmark utilities also measure individual pipeline components. Performance results are treated as environment-dependent measurements rather than hard architectural guarantees.

---

## Project Structure

```text
TOM/

├── .venv/                         # Dedicated Python virtual environment
│
├── config/                        # YAML configuration
│
├── python/                        # Python Brain
│   └── tom/
│       ├── agents/                # Agent state machine & orchestration
│       ├── core/                 # Core services, routing, context, lifecycle
│       ├── ipc/                  # Named Pipe client, transport & protocol
│       ├── models/               # Model provider abstractions/providers
│       ├── schemas/              # Pydantic v2 schemas
│       ├── security/             # Permission and confirmation systems
│       ├── telemetry/            # Structured logging & redaction
│       └── tools/                # Registry, executor, system & file tools
│
├── rust/                         # Rust Engine
│   └── tom-engine/               # Tokio runtime, IPC & low-level services
│
├── tests/
│   ├── unit/                     # Deterministic isolated unit tests
│   └── integration/              # Cross-component integration tests
│
├── pyproject.toml                # Python project metadata & tooling
├── IMPLEMENTATIONPLAN.md         # Master implementation roadmap
├── STATE.md                      # Current state & architectural decisions
├── PROGRESS.md                   # Chronological development ledger
├── HANDOFF.md                    # Next-session developer handoff
└── README.md                     # Project overview
```

> Phase-specific implementation plans are temporary development artifacts and are removed during phase closeout once their historical information has been migrated into the project documentation.

---

## Development Principles

1. **Python / Rust Dual-Core Architecture**
   Python manages intelligence, orchestration, agents, tools, and memory. Rust handles low-level OS operations, hardware/audio primitives, telemetry, and IPC.

2. **Strict `.venv` Isolation**
   All Python package installation and execution uses the repository's dedicated virtual environment.

3. **Deterministic Tools**
   Tools are typed, validated, bounded, and independently executable.

4. **Centralized Pre-Execution Permissions**
   Permission decisions occur before tool execution. Tools do not implement their own security policy.

5. **LLM / Agent Independence**
   Tools must never depend on specific LLMs, prompts, routers, or agent frameworks.

6. **Non-Bypassable Tool Execution**
   Agents and models must route tool execution through `ToolExecutor`.

7. **Security-First Design**
   Path traversal protection, secret redaction, bounded execution, explicit permissions, and safe failure are mandatory architectural properties.

8. **Cooperative Cancellation**
   Long-running operations must support bounded, explicit cancellation rather than uncontrolled background execution.

9. **Bounded Agent Execution**
   Agent loops use explicit step limits and deterministic termination conditions.

10. **Incremental Verification**
    Each implementation iteration must pass its relevant tests and quality gates before the project advances.

11. **Minimalism / Avoid Over-Engineering**
    New abstractions must solve a demonstrated requirement. Existing interfaces should be reused wherever practical.

12. **Local-First Model Runtime**
    TOM maintains a provider boundary so local model runtimes can be evaluated and replaced without coupling the agent framework to a particular inference engine.

---

## Documentation

- [Master Implementation Plan](IMPLEMENTATIONPLAN.md) — Overall TOM roadmap
- [State Snapshot](STATE.md) — Current implementation state and architectural decisions
- [Progress Ledger](PROGRESS.md) — Chronological development history
- [Developer Handoff](HANDOFF.md) — Next-session continuation point
- [Testing Guide](test.md) — Testing and verification walkthrough
- [Skills Index](skills/SKILL_INDEX.md) — Development skills and project-specific guidance

---

## Current Roadmap

The project is being developed incrementally through defined implementation phases.

```text
Phase 0  ── Bootstrap & Configuration          COMPLETE
   │
Phase 1  ── Rust Engine & IPC                  COMPLETE
   │
Phase 2  ── Python Core & IPC Client           COMPLETE
   │
Phase 3  ── Tools & Security                   COMPLETE
   │
Phase 4  ── Agents & Local Model Routing      IMPLEMENTATION COMPLETE
   │
Phase 5  ── Next subsystem                     NEXT
   │
   ▼
Future phases ── Memory, Voice, Vision,
                 Web, Security hardening,
                 Concurrency, Monitoring,
                 Personality & deployment
```

Phase 5 should begin only after the Phase 4 documentation closeout has been completed and the repository state has been verified.

---

## Design Goal

TOM is intended to evolve into a **local-first personal AI operating layer** where:

- intelligence remains modular,
- tools remain deterministic,
- model providers remain replaceable,
- sensitive operations remain permission-controlled,
- low-level system operations remain isolated,
- resource usage remains bounded,
- and every major architectural step is verified before the next one begins.
