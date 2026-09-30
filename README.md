# TOM

> A local, modular, privacy-oriented personal AI assistant and operating layer.

---

## Current Status

| Milestone   | Subsystem / Focus                                                                   | Status       |   Tests |
| ----------- | ----------------------------------------------------------------------------------- | ------------ | ------: |
| **Phase 0** | Project Bootstrap, Configuration & Structured Logging                               | **COMPLETE** |       9 |
| **Phase 1** | Rust Engine (`tom-engine`) — Lifecycle, Telemetry & IPC Server                      | **COMPLETE** |      79 |
| **Phase 2** | Python Core & IPC Client — `NamedPipeIpcClient`, `EngineClient`, `LifecycleManager` | **COMPLETE** |     168 |
| **Phase 3** | Deterministic Tools & Permission Engine                                             | **COMPLETE** |     406 |
| **Phase 4** | Agent Framework & Local Model Routing                                               | **COMPLETE** |     621 |
| **Phase 5** | Memory Architecture (SQLite Store, Qdrant Index, CPU Embeddings, Policies)         | **COMPLETE** |     621 |
| **Phase 6** | Voice Pipeline (Audio IPC, STT, TTS, Barge-in, Interaction Layer, Voice Tools)      | **COMPLETE** | **931** |

- Verified repository test baseline: **848 Python tests (756 unit + 92 integration) + 83 Rust tests = 931 tests passing**.
- All quality gates clean: Ruff checks (0 violations), Ruff format (0 diffs), Cargo fmt (0 diffs), Cargo clippy (0 warnings).

### Current Development State

**Phase 6 — Voice Pipeline: CLOSED & COMPLETE**

All 5 iterations of Phase 6 are implemented, validated, and formally closed:
- Audio control IPC over Windows Named Pipe (`\\.\pipe\tom-engine`) via Rust `AudioManager`.
- `STTProvider` abstraction with lazy CPU/int8 `FasterWhisperSTTProvider` and deterministic mock.
- `TTSProvider` abstraction with lazy CPU `KokoroTTSProvider` and deterministic mock.
- Deterministic `SpeechFormatter` converting markdown and symbols into natural speakable text.
- Formal `VoicePipelineManager` state machine (`IDLE`, `LISTENING`, `PROCESSING`, `SPEAKING`, `INTERRUPTED`, `ERROR`) with sub-millisecond barge-in interruption and epoch invalidation.
- `VoiceInteractionManager` session coordination maintaining bounded ephemeral `ConversationHistory` in RAM.
- Deterministic agent voice tools (`voice.announce`, `voice.status`) routed through `ToolExecutor` $\to$ `PermissionEngine`.

Phase 7 (Vision & Multimodal Capabilities / Extended OS Automation) is next in the master roadmap.

---

## Architecture

TOM uses a dual-core architecture:

- **Python Brain** — high-level orchestration, agents, model routing, tools, memory, voice interaction layer, and future intelligence.
- **Rust Engine (`tom-engine`)** — deterministic low-level OS operations, audio capture/playback buffers, hardware telemetry, and Windows Named Pipe IPC.

```text
User / Modality (Voice or Text)
  │
  ├─────────────────────────────────────────────────┐
  │                                                 │
  ▼                                                 ▼
VoiceInteractionManager                       IntentRouter
  │ (ephemeral RAM context)                         │ (two-tier heuristic + model fallback)
  ▼                                                 ▼
VoicePipelineManager                          Agent / Orchestrator
  │ (STT -> Agent -> Formatter -> TTS)              │
  ▼                                                 ▼
EngineClient ───────────────────────────────► ToolExecutor
  │ (audio IPC & telemetry)                         │
  │                                           PermissionEngine + ConfirmationHook
  │                                                 │
  │                                     ┌───────────┴───────────┐
  │                                     │                       │
  │                                System Tools            File Tools
  │                                Memory Tools            Voice Tools
  │                                     │                       │
  │                                     └───────────┬───────────┘
  ▼                                                 ▼
NamedPipeIpcClient ─────────────────────────────────┘
  │
Windows Named Pipe (\\.\pipe\tom-engine)
  │
Rust tom-engine
  ├── IPC Server & Dispatcher
  ├── AudioManager (audio.capture_start/stop, audio.get_speech, audio.play_buffer, audio.playback_stop)
  └── Telemetry (CPU, memory, GPU, disk, battery, processes)
```

---

## Subsystem Highlights

### Phase 6 — Voice Pipeline
- **Decoupled Audio Transport** — Utterance-buffered requests over Named Pipe IPC (`audio.*` endpoints).
- **Abstracted Local Providers** — Provider ABCs with lazy loading; CPU-first defaults preserve GPU VRAM for the primary LLM.
- **Deterministic Speech Formatting** — Sub-millisecond text normalization and markdown removal.
- **Instant Barge-In** — Audio playback stops immediately, active tasks cancel, and turn epochs increment to prevent stale playback.
- **RAM-Only Ephemeral Context** — Prevents conversational audio turns from polluting long-term vector memory.

### Phase 5 — Memory Subsystem
- **Authoritative SQLite Store** — ACID persistence with CRUD, keyword search, and TTL expiration.
- **Optional Qdrant Vector Index** — Semantic search with automatic fallback to SQLite when offline.
- **CPU Embeddings** — 384-dimensional CPU embeddings preserve GPU VRAM for inference.
- **MemoryPolicy Guardrails** — Secret filtering, credential detection, entropy checks, and confirmation requirements.
- **Deterministic Memory Tools** — `memory.remember`, `memory.recall`, `memory.forget`, `memory.recent`, `memory.preferences`.

### Phase 3 & 4 — Tools & Agent Foundation
- **Non-Bypassable ToolExecutor** — All tools execute through centralized permission gating (`SAFE`, `ASK_USER`, `BLOCK`).
- **20 Default Built-In Tools** — 7 system tools, 6 file tools, 5 memory tools, 2 voice tools.
- **Deterministic Agent Lifecycle** — 6-state machine with cancellation propagation and error recovery.

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
