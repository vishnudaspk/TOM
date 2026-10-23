# TOM

> A local, modular, privacy-oriented personal AI assistant and operating layer.

---

## What is TOM?

TOM is a **local-first personal AI operating layer** designed to run entirely on your hardware. It combines a Python-based "brain" for intelligence, orchestration, and reasoning with a Rust-based "engine" for low-level system operations, audio I/O, and hardware telemetry. All communication happens over Windows Named Pipes — no network exposure, no cloud dependencies.

**Core philosophy:**
- **Local-first** — Models, memory, and tools run on your machine
- **Modular** — Swap model providers, memory backends, or tool sets without rewiring
- **Deterministic** — Tools are typed, validated, and independently executable
- **Permission-controlled** — Every sensitive action requires explicit consent
- **Bounded** — Resource usage, execution time, and agent loops have hard limits

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        USER INTERACTION                         │
│  (Voice via Mic/Speakers  •  Text via CLI/API)                  │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                   PYTHON BRAIN (tom/)                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │
│  │ Voice        │  │ Intent       │  │ Agent        │           │
│  │ Interaction  │──│ Router       │──│ Orchestrator │           │
│  │ Manager      │  │ (heuristic + │  │ (6-state     │           │
│  │ (VAD, STT,   │  │  model fallback)        machine)      │           │
│  │  TTS, turn   │  │              │  │              │           │
│  │  mgmt)       │  │              │  │              │           │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘           │
│         │                 │                 │                    │
│         ▼                 ▼                 ▼                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                    TOOL EXECUTOR                          │   │
│  │  (PermissionEngine → ConfirmationHook → Tool Registry)   │   │
│  └────────────────────────────┬──────────────────────────────┘   │
│                               │                                   │
│         ┌─────────────────────┼─────────────────────┐             │
│         ▼                     ▼                     ▼             │
│  ┌─────────────┐       ┌─────────────┐       ┌─────────────┐     │
│  │ System Tools│       │ File Tools  │       │Memory Tools │     │
│  │ (7 tools)   │       │ (6 tools)   │       │ (5 tools)   │     │
│  └─────────────┘       └─────────────┘       └─────────────┘     │
│         │                     │                     │             │
│         └─────────────────────┼─────────────────────┘             │
│                               ▼                                   │
│                    ┌─────────────────────┐                         │
│                    │  Voice Tools (2)    │                         │
│                    └──────────┬──────────┘                         │
└───────────────────────────────│───────────────────────────────────┘
                                │ Named Pipe IPC (\\.\pipe\tom-engine)
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    RUST ENGINE (tom-engine)                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │
│  │ IPC Server   │  │ AudioManager │  │ Telemetry    │           │
│  │ & Dispatcher │  │ (capture,    │  │ (CPU, mem,   │           │
│  │              │  │  playback,   │  │  GPU, disk,  │           │
│  │              │  │  VAD, barge) │  │  battery)    │           │
│  └──────────────┘  └──────────────┘  └──────────────┘           │
└─────────────────────────────────────────────────────────────────┘
```

### Dual-Core Design

| Layer | Language | Responsibility |
|-------|----------|----------------|
| **Python Brain** | Python 3.11+ | Agents, model routing, tools, memory, voice interaction, reasoning |
| **Rust Engine** | Rust 1.80+ | Audio I/O, hardware telemetry, IPC server, low-level OS ops |

**IPC Protocol:** Windows Named Pipes (`\\.\pipe\tom-engine`) — sub-millisecond roundtrips, zero network stack.

---

## Current Status (as of 2026-10-23)

### Completed Phases

| Phase | Subsystem | Status | Test Count |
|-------|-----------|--------|------------|
| 0 | Bootstrap, Config, Structured Logging | ✅ Complete | 9 |
| 1 | Rust Engine — Lifecycle, Telemetry, IPC Server | ✅ Complete | 79 (Rust) |
| 2 | Python Core & IPC Client | ✅ Complete | 168 |
| 3 | Deterministic Tools & Permission Engine | ✅ Complete | 406 |
| 4 | Agent Framework & Local Model Routing | ✅ Complete | 621 |
| 5 | Memory Architecture (SQLite, Qdrant, CPU Embeddings, Policies) | ✅ Complete | 621 |
| 6 | Voice Pipeline (Audio IPC, STT, TTS, Barge-in, Voice Tools) | ✅ Complete | 931 |
| 7 | Vision & Multimodal Capabilities | ✅ Complete | 1,087 |
| 8 | Proactive Agent & Long-Horizon Tasks | ✅ Complete | 1,301 (+8 skipped) |
| 9 | Interaction Core (9.0–9.4: VAD, streaming STT/TTS, turn mgmt) | ✅ Complete | 833 targeted + 317 voice |

**Last full-repo benchmark:** Phase 8 exit (2026-10-02) — **1,301 passed** (1,218 Python + 83 Rust), 8 skipped.

### What's Working Today

- ✅ **Voice interaction** — Wake-word-free, push-to-talk, or VAD-activated with instant barge-in
- ✅ **Local LLM routing** — Generic OpenAI-compatible provider (currently llama.cpp at `http://127.0.0.1:65400/v1`)
- ✅ **20+ deterministic tools** — System, file, memory, voice — all permission-gated
- ✅ **Persistent memory** — SQLite (authoritative) + optional Qdrant vector index with CPU embeddings
- ✅ **Proactive agent** — Long-horizon task planning with cancellation propagation
- ✅ **Vision/multimodal** — Image analysis via local models
- ✅ **Quality gates clean** — Ruff, cargo fmt, cargo clippy all passing

---

## Current Hurdles & Areas for Improvement

### 1. **Model Provider Stability** (High Priority)
- **Issue:** llama.cpp server (`llama-server`) occasionally OOMs or hangs on long contexts (>8k tokens)
- **Impact:** Agent loops stall; requires manual restart
- **Mitigation needed:** Health checks, automatic fallback, context window management

### 2. **Voice Pipeline Latency** (Medium Priority)
- **Current:** ~800-1,200ms end-to-end (VAD → STT → LLM → TTS → playback)
- **Target:** <500ms for natural conversation
- **Bottlenecks:** STT (FasterWhisper CPU int8), TTS (Kokoro CPU), LLM first-token latency

### 3. **Memory Retrieval Quality** (Medium Priority)
- **Issue:** Vector search (Qdrant) returns semantically similar but contextually irrelevant results
- **Missing:** Hybrid search (keyword + vector), re-ranking, temporal decay weighting
- **Impact:** Agent "remembers" wrong context for multi-session tasks

### 4. **No Persistent Agent State** (Medium Priority)
- **Issue:** Agent state (goals, plans, tool call history) lives only in RAM
- **Impact:** Restart loses all in-progress work; no checkpoint/resume
- **Needed:** SQLite-backed agent state machine persistence

### 5. **Single-User, Single-Session** (Architectural)
- **Current:** One `VoiceInteractionManager` = one conversation
- **Missing:** Multi-user isolation, concurrent sessions, session handoff
- **Blocker:** Audio IPC and ephemeral context are globally scoped

### 6. **Windows-Only Audio IPC** (Platform Lock-in)
- **Current:** Named Pipes + `cpal`/`rodio` for audio
- **Blocker:** Porting to Linux/macOS requires rewriting audio transport layer
- **Path forward:** Abstract `AudioTransport` trait; add Unix domain socket + PipeWire/PulseAudio backends

### 7. **Testing Gaps** (Ongoing)
- **Missing:** End-to-end voice integration tests (require hardware)
- **Missing:** Chaos testing (IPC disconnect, OOM, model timeout)
- **Missing:** Performance regression benchmarks in CI

---

## Development Environment

| Component | Version |
|-----------|---------|
| OS | Windows 11 |
| Python | 3.11.9 |
| Rust | 1.80+ |
| Async Runtime | Tokio 1.43 |
| Testing | pytest 9.1.1 |
| Linting/Format | Ruff 0.16.7 |
| IPC | Windows Named Pipes |
| Python Env | Repository `.venv` (mandatory) |

### Setup

```powershell
# Create venv if needed
python -m venv .venv

# Verify interpreter
.\.venv\Scripts\python.exe -c "import sys; print(sys.executable)"

# Install TOM in development mode
.\.venv\Scripts\python.exe -m pip install -e .
```

### Running Tests & Quality Gates

```powershell
# Python tests
.\.venv\Scripts\pytest.exe tests/unit/ -v
.\.venv\Scripts\pytest.exe tests/integration/ -v

# Ruff
.\.venv\Scripts\ruff.exe check python/ tests/
.\.venv\Scripts\ruff.exe format --check python/ tests/

# Rust tests
cd rust\tom-engine
cargo test
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cd ..\..
```

---

## Project Structure

```
TOM/
├── .venv/                         # Dedicated Python virtual environment
├── config/                        # YAML configuration
├── python/                        # Python Brain
│   └── tom/
│       ├── agents/                # Agent state machine & orchestration
│       ├── core/                  # Core services, routing, context, lifecycle
│       ├── ipc/                   # Named Pipe client, transport & protocol
│       ├── models/                # Model provider abstractions/providers
│       ├── schemas/               # Pydantic v2 schemas
│       ├── security/              # Permission and confirmation systems
│       ├── telemetry/             # Structured logging & redaction
│       └── tools/                 # Registry, executor, system & file tools
├── rust/                          # Rust Engine
│   └── tom-engine/                # Tokio runtime, IPC & low-level services
├── tests/
│   ├── unit/                      # Deterministic isolated unit tests
│   └── integration/               # Cross-component integration tests
├── pyproject.toml                 # Python project metadata & tooling
├── IMPLEMENTATIONPLAN.md          # Master implementation roadmap
├── STATE.md                       # Current state & architectural decisions
├── PROGRESS.md                    # Chronological development ledger
├── HANDOFF.md                     # Next-session developer handoff
└── README.md                      # This file
```

---

## Design Principles

1. **Python/Rust Dual-Core** — Intelligence in Python, primitives in Rust
2. **Strict `.venv` Isolation** — Never install globally
3. **Deterministic Tools** — Typed, validated, bounded, independently executable
4. **Centralized Pre-Execution Permissions** — Policy before execution
5. **LLM/Agent Independence** — Tools never couple to specific models
6. **Non-Bypassable Tool Execution** — All tools route through `ToolExecutor`
7. **Security-First** — Path traversal protection, secret redaction, bounded execution
8. **Cooperative Cancellation** — Explicit cancellation over uncontrolled background work
9. **Bounded Agent Execution** — Step limits, deterministic termination
10. **Incremental Verification** — Tests + quality gates before advancing
11. **Minimalism** — No abstraction without demonstrated need
12. **Local-First Model Runtime** — Provider boundary for swapability

---

## Documentation

- [Master Implementation Plan](IMPLEMENTATIONPLAN.md) — Overall roadmap
- [State Snapshot](STATE.md) — Current implementation state & decisions
- [Progress Ledger](PROGRESS.md) — Chronological development history
- [Developer Handoff](HANDOFF.md) — Next-session continuation point
- [Testing Guide](test.md) — Testing and verification walkthrough
- [Skills Index](skills/SKILL_INDEX.md) — Development skills & project guidance

---

## Roadmap

```
Phase 0  ── Bootstrap & Configuration          ✅ COMPLETE
Phase 1  ── Rust Engine & IPC                  ✅ COMPLETE
Phase 2  ── Python Core & IPC Client           ✅ COMPLETE
Phase 3  ── Tools & Security                   ✅ COMPLETE
Phase 4  ── Agents & Local Model Routing       ✅ COMPLETE
Phase 5  ── Memory Architecture                ✅ COMPLETE
Phase 6  ── Voice Pipeline                     ✅ COMPLETE
Phase 7  ── Vision & Multimodal                ✅ COMPLETE
Phase 8  ── Proactive Agent & Long-Horizon     ✅ COMPLETE
Phase 9  ── Interaction Core (9.0–9.4)         ✅ COMPLETE
Phase 9.5/9.6 ── Deferred (streaming polish)   ⏸️  ON HOLD
Phase 10 ── ???                                🚫  REQUIRES EXPLICIT APPROVAL
```

**Next meaningful work** (requires approval):
- Agent state persistence (survive restarts)
- Hybrid memory retrieval (keyword + vector + rerank)
- Voice latency optimization (<500ms target)
- Cross-platform audio transport abstraction
- Multi-session / multi-user support

---

## Design Goal

TOM evolves into a **local-first personal AI operating layer** where:

- Intelligence remains **modular**
- Tools remain **deterministic**
- Model providers remain **replaceable**
- Sensitive operations remain **permission-controlled**
- Low-level system operations remain **isolated**
- Resource usage remains **bounded**
- Every major architectural step is **verified** before the next begins