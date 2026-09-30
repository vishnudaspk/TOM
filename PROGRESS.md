# TOM Development Progress

## Phase Summary Ledger

| Phase | Subsystem / Focus | Status | Tests Verified | Closure Date |
|---|---|---|---|---|
| **Phase 0** | Project Setup, Repo Scaffolding & Toolchains | CLOSED | Verified | 2026-09-12 |
| **Phase 1** | Rust Engine (`tom-engine`) & System Telemetry | CLOSED | 79 passed | 2026-09-13 |
| **Phase 2** | Python Core Engine & Named Pipe IPC Client | CLOSED | 168 passed | 2026-09-14 |
| **Phase 3** | Deterministic Tool System, Permissions & Sandbox | CLOSED | 406 passed | 2026-09-17 |
| **Phase 4** | Agent Framework, Lifecycle & Local Model Routing | CLOSED | 621 passed | 2026-09-19 |
| **Phase 5** | Memory Architecture (SQLite Relational + Qdrant Vector) | CLOSED | 621 passed | 2026-09-24 |
| **Phase 6** | Voice Pipeline (Audio IPC + STT + TTS + Barge-in) | CLOSED | 931 passed (848 Py + 83 Rust) | 2026-09-30 |
| **Phase 7** | Vision & Multimodal Capabilities / Extended OS Automation | PENDING | — | In Planning |

---

## Latest Verified Test Baseline

- **Total Python Tests**: 848 passed (756 unit + 92 integration)
  - Unit tests: 756 passed (180 voice + 123 tools + 149 IPC + 102 agents + 61 models + 54 core + 50 memory + 33 security + 4 telemetry)
  - Integration tests: 92 passed (23 voice + 32 tools + 15 python_rust + 13 agents + 6 memory + 3 models)
- **Total Rust Tests**: 83 passed (76 unit + 7 integration in `tom-engine`)
- **Combined Repository Total**: 931 tests passing across the repository
- **Quality Gates**: Ruff check clean (0 violations), Ruff format clean (0 diffs), Cargo fmt clean, Cargo clippy clean (0 warnings)
- **Baseline Date**: 2026-09-30

---

## Phase History & Architectural Milestones

### Phase 0: Project Setup & Environment Scaffolding
- **Objective**: Establish dual Python/Rust workspace, configuration schemas, dependency policies, and baseline tooling.
- **Outcomes**: Configured `.venv` with Python 3.11+, Rust workspace with `tom-engine`, shared directories (`config/`, `data/`, `models/`), Ruff linter/formatter, Pytest, and Cargo verification gates.
- **Status**: CLOSED.

### Phase 1: Rust Engine Foundations
- **Objective**: Build the low-level, high-performance operating engine in Rust.
- **Outcomes**: Built `tom-engine` with asynchronous Tokio runtime, Windows Named Pipe IPC server (`\\.\pipe\tom-engine`), system telemetry monitors (CPU, memory, GPU, disk, battery, processes), structured logging, and internal event bus.
- **Verification**: 79 Rust tests passing; zero warnings.
- **Status**: CLOSED.

### Phase 2: Python Core & IPC Client
- **Objective**: Establish the Python Brain core and robust IPC communication with `tom-engine`.
- **Outcomes**: Built `NamedPipeIpcClient` with length-prefixed JSON-RPC wire protocol, `EngineClient` typed facade, auto-reconnection backoff, and lifecycle management.
- **Verification**: 168 tests passing across Python and Rust.
- **Status**: CLOSED.

### Phase 3: Deterministic Tools & Security
- **Objective**: Provide a non-bypassable, deterministic tool execution engine with strong permission boundaries.
- **Outcomes**: Implemented `ToolRegistry`, `ToolExecutor`, `PermissionEngine` (3-tier security: `SAFE`, `ASK_USER`, `BLOCK`), `ConfirmationHook`, filesystem `PathGuard` sandbox, and 13 initial system and file tools.
- **Verification**: 406 tests passing; zero security bypass regressions.
- **Status**: CLOSED.

### Phase 4: Agent Framework & Local Model Routing
- **Objective**: Implement autonomous agent loops, context management, and local model routing.
- **Outcomes**: Implemented explicit 6-state agent lifecycle (`IDLE`, `THINKING`, `ACTING`, `WAITING_CONFIRMATION`, `ERROR`, `TERMINATED`), `AgentOrchestrator`, two-tier intent routing (heuristics + model), abstract `LLMProvider` interface (supporting mock and local OpenAI-compatible endpoints like LM Studio/Bionic), and cooperative cancellation.
- **Verification**: 621 tests passing.
- **Status**: CLOSED.

### Phase 5: Memory Architecture
- **Objective**: Provide hybrid relational and semantic long-term memory for TOM.
- **Outcomes**: Built `MemoryManager` single entry point, authoritative SQLite relational store, optional Qdrant vector index, local CPU sentence embeddings (preserving GPU VRAM), `MemoryPolicy` secret/credential filtering, and 5 deterministic memory tools behind `ToolExecutor`.
- **Verification**: 621 tests passing; secret filtering fully audited.
- **Status**: CLOSED.

### Phase 6: Voice Pipeline
- **Objective**: Implement local, low-latency, conversational voice capabilities with interruption support.
- **Outcomes**:
  - Rust `AudioManager`: Audio capture and buffered playback over Named Pipe IPC (`audio.*` handlers).
  - STT Layer: `STTProvider` ABC with `FasterWhisperSTTProvider` (lazy loading, CPU/int8 default, `asyncio.to_thread` inference).
  - TTS Layer: `TTSProvider` ABC with `KokoroTTSProvider` (lazy loading, CPU default) and deterministic `SpeechFormatter`.
  - Pipeline & Interruption: `VoicePipelineManager` formal state machine (`IDLE -> LISTENING -> PROCESSING -> SPEAKING`) with instant barge-in playback cancellation and turn epoch invalidation.
  - Interaction & Modality: `VoiceInteractionManager` coordinating conversational turns with bounded ephemeral `ConversationHistory` in volatile RAM (preventing vector space dilution).
  - Tools: Registered `voice.announce` and `voice.status` (20 default built-in tools total).
- **Verification**: 931 total verified tests (848 Python + 83 Rust); verified zero regressions.
- **Status**: CLOSED & COMPLETE.

---

## Architectural Decisions Summary (Phase 6)

- **Decision 041: Decoupled Audio Transport over Named Pipe IPC**  
  Audio IPC uses discrete utterance-buffered requests over `\\.\pipe\tom-engine` (`audio.capture_start`, `audio.capture_stop`, `audio.get_speech`, `audio.play_buffer`, `audio.playback_stop`), avoiding streaming binary pipes and polling.
- **Decision 042: STTProvider Abstraction & FasterWhisper Lazy Provider**  
  `STTProvider` is the sole abstraction boundary. `FasterWhisperSTTProvider` imports lazily at use time. Inference runs via `asyncio.to_thread()` on CPU to preserve GPU VRAM for the primary LLM.
- **Decision 043: TTSProvider Abstraction, SpeechFormatter & Kokoro Lazy Provider**  
  `TTSProvider` is the sole abstraction boundary. `KokoroTTSProvider` imports lazily on CPU. `SpeechFormatter` deterministically strips markdown and normalizes symbols without external NLP dependencies.
- **Decision 044: VoicePipelineManager State Machine & Barge-In Architecture**  
  Enforces `IDLE -> LISTENING -> PROCESSING -> SPEAKING` with priority barge-in `SPEAKING -> INTERRUPTED`. Barge-in stops audio playback immediately via `EngineClient.stop_audio_playback()` and invalidates the turn epoch counter.
- **Decision 045: Voice as Outer Interaction Layer (Not LLM Toolset)**  
  `VoiceInteractionManager` manages sessions in RAM. Raw audio turns do not pollute persistent SQLite/Qdrant vector stores. Only `voice.announce` and `voice.status` are tools (`SAFE`). Voice listening and speaking are outer interaction modalities.

---

## Measured Software Latency Benchmarks (Phase 6 Verified)

| Benchmark Metric | Iterations | Min | Median | P95 | Max | Target | Status |
|---|---|---|---|---|---|---|---|
| **SpeechFormatter (Short Text)** | 100 | 0.022 ms | **0.025 ms** | 0.031 ms | 1.279 ms | < 5 ms | PASSED |
| **SpeechFormatter (Medium Text)** | 100 | 0.040 ms | **0.041 ms** | 0.048 ms | 0.062 ms | < 10 ms | PASSED |
| **SpeechFormatter (Markdown-Heavy)** | 100 | 0.061 ms | **0.064 ms** | 0.110 ms | 0.235 ms | < 15 ms | PASSED |
| **Turn Orchestration Overhead** | 50 | 0.103 ms | **0.110 ms** | 0.229 ms | 0.253 ms | < 25 ms | PASSED |
| **Barge-in Stop & Invalidation** | 10 | 0.065 ms | **0.084 ms** | 0.125 ms | 0.142 ms | < 10 ms | PASSED |
