# TOM Implementation Plan

## 1. Implementation Philosophy

TOM (The Operating Mind) is a local-first, voice-enabled personal AI assistant acting as an intelligent operating layer over the user's computer. TOM's design adheres strictly to the following core tenets:

1. **TOM ≠ LLM**: The Large Language Model provides linguistic reasoning and semantic inference. TOM's determinism, state machine, permission gates, memory persistence, tool execution, and resource scheduling reside in deterministic code outside the LLM.
2. **Dual-Core Architecture (Python Brain + Rust Engine)**:
   - **Python Brain**: AI orchestration, agent planning, Pydantic-typed tool dispatch, memory policies, model routing, and contextual reasoning.
   - **Rust Engine (`tom-engine`)**: Always-on nervous system, low-latency audio capture/playback, wake-word detection, Voice Activity Detection (VAD), system metric probes, hotkeys, input simulation primitives, and local IPC server.
3. **Deterministic Superiority**: Never delegate to an LLM what can be executed deterministically. System operations, validation, IPC serialization, file manipulation, and security policy checks must run in predictable, verifiable code.
4. **Strict Security by Design**: All tool invocations and state changes pass through a non-bypassable permission layer (`SAFE`, `ASK USER`, `BLOCK`). Secrets and credentials never leak into logs, LLM prompts, or telemetry.
5. **Rigorous Hardware Awareness**: Designed around a single mid-range laptop workstation with an 8GB RTX 4060 GPU, 16GB system RAM, and modern multi-core CPU. Models cannot all reside in VRAM simultaneously; explicit VRAM/RAM resource lifecycle management is mandatory.
6. **Streaming & Low Latency as First-Class Concerns**: Every interactive pipeline stage (`STT → Router → Reasoning → Speech Formatter → TTS → Audio Playback`) must stream incrementally where possible to minimize perceived response time.

---

## 2. Architecture Overview

### 2.1 System Topography

```text
                                  TOM
                                   │
                         ┌─────────┴─────────┐
                         │                   │
                     Python                 Rust
                   TOM BRAIN             tom-engine
                         │                   │
                         │          ┌────────┼────────┐
                         │          │        │        │
                         │        Audio    System    IPC
                         │        Wake     Monitor  Events
                         │        Word     Hotkeys
                         │
           ┌─────────────┼───────────────────────────────┐
           │             │               │               │
           ▼             ▼               ▼               ▼
        Router          LLM           Memory           Tools
       Qwen3-1.7B     Qwen3-8B      SQLite/Qdrant        │
           │             │                               │
           ▼             ▼                         ┌─────┼─────┐
      Intent/Model   Reasoning                     ▼     ▼     ▼
        Routing      Planning                    System Files Web
                         │
                         ▼
                       Vision
                     Fast CV/VLM
```

### 2.2 Subsystem Separation

| Subsystem | Host | Primary Technology | Responsibility |
|-----------|------|--------------------|----------------|
| **tom-engine** | Rust | Tokio, Windows Named Pipes, CPAL/Rodio, Sysinfo | Low-level OS telemetry, hotkeys, audio capture/playback, IPC server |
| **Orchestrator & Agents** | Python | Pydantic AI, Asyncio | Main event loop, task state machine, agent delegation, multi-step planning |
| **Model Layer** | Python | llama-cpp-python / vLLM / ONNX Runtime | Router (Qwen3-1.7B), Reasoning (Qwen3-8B), STT (Whisper), TTS (Kokoro) |
| **Resource Manager** | Python | psutil, PyNVML / Torch CUDA | VRAM budget tracking (8GB limit), model load/unload, GPU locks |
| **Memory System** | Python | SQLite (authoritative), Qdrant (semantic) | Single entry `MemoryManager`, policy enforcement, retention |
| **Tool Registry & Executor** | Python | Pydantic v2 schemas | Permission checks (`SAFE`/`ASK`/`BLOCK`), path validation, timeouts |
| **Security & Auditing** | Python | Cryptography, regex scrubbers | Secret filtering, confirmation prompt dispatcher, audit log |

---

## 3. Development Phases

---

### Phase 0 — Foundation / Project Bootstrap

- **Objective**: Establish the complete repository foundation, workspace directory structure, Python environment configuration, linting/formatting rules, configuration schemas, structured logging with secret redaction, and CI/test harness.
- **Prerequisites**: Python 3.11+ installed; Git repository initialized.
- **Components**:
  - `pyproject.toml` (standard packaging, dependency pins, Ruff/Pytest configs)
  - `.gitignore` (hardened against secrets, model weights, local databases, build artifacts)
  - `python/tom/` (core directory tree conforming to plan.md §46)
  - `python/tom/telemetry/logging.py` (structured JSON logging + automatic secret redactor)
  - `python/tom/schemas/config.py` & `python/tom/core/config.py` (Pydantic-based configuration system)
  - `config/*.yaml` (baseline configuration templates for TOM)
  - `tests/unit/core/` & `tests/unit/telemetry/` (initial test suite)
- **Skills**: `git/workflow`, `documentation/project-state`, `coding/structured-logging`, `coding/validation`.
- **Dependencies**: `pydantic>=2.0`, `pyyaml`, `pytest`, `structlog` (or robust stdlib JSON logging), `ruff`.
- **Implementation Tasks**:
  1. Create directory structure matching plan.md §46.
  2. Author `pyproject.toml` with pinned development and runtime dependencies.
  3. Expand `.gitignore` to strictly exclude data, cache, logs, models, and virtual environments.
  4. Implement structured JSON logger with `SENSITIVE_FIELD_NAMES` secret filtering.
  5. Implement Pydantic-based YAML configuration schema and loader.
  6. Create baseline config files in `config/`.
  7. Write unit tests for config validation and structured logging.
- **Testing**:
  - `pytest tests/unit/core/test_config.py`: Verify valid config loading, missing field handling, type enforcement.
  - `pytest tests/unit/telemetry/test_logging.py`: Verify JSON output structure, timing fields, secret redacting.
- **Security Considerations**: Ensure `.env` and credential files are strictly gitignored. Validate that logger redacts keys matching `api_key`, `token`, `password`, `secret`.
- **Performance Considerations**: Fast startup time (< 50ms) for foundational configuration and logging.
- **Completion Criteria**: Directory tree exists; configuration loads and validates via Pydantic; tests pass clean; `.gitignore` validated.
- **Exit Criteria**: All foundational tests pass; project state documents (`STATE.md`, `PROGRESS.md`, `HANDOFF.md`) reflect baseline readiness.
- **Potential Risks**: Inconsistent package versions or missing native build tools.
- **Rollback / Recovery**: Revert commits; reset configuration templates.

---

### Phase 1 — Rust Engine / tom-engine

- **Objective**: Build the always-on Rust background engine (`tom-engine`) providing the IPC server (Windows Named Pipes), system monitoring (CPU/RAM/GPU/Disk/Battery), event bus, audio capture primitives, and hotkey handlers.
- **Prerequisites**: Phase 0 complete; Rust toolchain (`cargo`, `rustc` 1.75+) installed on host.
- **Components**:
  - `rust/tom-engine/Cargo.toml`
  - `rust/tom-engine/src/ipc/` (`server.rs`, `protocol.rs`, `handlers.rs`)
  - `rust/tom-engine/src/system/` (`cpu.rs`, `gpu.rs`, `memory.rs`, `battery.rs`, `processes.rs`)
  - `rust/tom-engine/src/events/` (`event.rs`, `types.rs`, `dispatch.rs`)
  - `rust/tom-engine/src/audio/` (`capture.rs`, `playback.rs`, `device.rs`)
- **Skills**: `rust/async-tokio`, `rust/ipc`, `rust/error-handling`, `rust/event-system`, `rust/testing`, `system-design/python-rust-boundary`.
- **Dependencies**: `tokio`, `serde`, `serde_json`, `tracing`, `tracing-subscriber`, `sysinfo`, `windows-sys` / `interprocess`.
- **Implementation Tasks**:
  1. Initialize `tom-engine` Cargo crate with Tokio async runtime.
  2. Implement IPC protocol framing (length-prefixed or line-delimited JSON over Windows Named Pipe `\\.\pipe\tom-engine`).
  3. Implement IPC request dispatcher with protocol version checking and error responses.
  4. Implement system telemetry monitors (CPU%, RAM used, GPU metrics via NVML/DXGI).
  5. Implement internal broadcast event bus for asynchronous notifications.
  6. Add unit tests for protocol serialization, handler dispatch, and metric collection.
- **Testing**:
  - Unit tests: `cargo test` covering message deserialization, version mismatch rejection, telemetry parsing.
  - Integration test: Local named pipe mock client sending queries and receiving valid JSON responses.
- **Security Considerations**: Named pipe permissions must restrict access to the current Windows user SID. Reject malformed packets without crashing.
- **Performance Considerations**: Polling telemetry should consume < 1% CPU; IPC request roundtrip latency < 2ms.
- **Completion Criteria**: Rust engine compiles cleanly; starts Named Pipe server; accurately reports system metrics via IPC test.
- **Exit Criteria**: `cargo test` passes 100%; mock pipe client successfully exchanges round-trip messages.
- **Potential Risks**: Windows-specific IPC quirks; NVML/GPU driver access permissions.
- **Rollback / Recovery**: Disable GPU monitoring module, fall back to pure sysinfo CPU/RAM.

---

### Phase 2 — Python Core & IPC Client

- **Objective**: Implement the Python-side IPC client communicating with `tom-engine`, typed schemas for all inter-process messages, connection retry/reconnect logic, and base lifecycle management.
- **Prerequisites**: Phase 1 complete (or mocked IPC socket for unit testing).
- **Components**:
  - `python/tom/ipc/client.py` (Async Windows named pipe client)
  - `python/tom/ipc/protocol.py` (Pydantic models for IPC requests/responses)
  - `python/tom/ipc/errors.py` (IPC connection, timeout, and protocol exceptions)
  - `python/tom/core/lifecycle.py` (Startup/shutdown coordinator)
- **Skills**: `python/ipc-client`, `coding/validation`, `coding/structured-logging`, `testing/python-testing`.
- **Dependencies**: `asyncio`, `pydantic`, `pywin32` (or `asyncio` named pipe support).
- **Implementation Tasks**:
  1. Define Pydantic models for IPC `Request`, `Response`, and `EngineEvent`.
  2. Implement `AsyncNamedPipeClient` with auto-reconnection, health check ping, and timeout cancellation.
  3. Integrate structured logging and tracing spans on every IPC call.
  4. Build mock engine runner for deterministic Python unit testing without compiling Rust.
- **Testing**:
  - `pytest tests/unit/ipc/`: Connection recovery, protocol mismatch rejection, timeout handling.
  - `pytest tests/integration/python_rust/`: Live communication against `tom-engine`.
- **Security Considerations**: Strict validation of incoming IPC payloads before passing to internal handlers.
- **Performance Considerations**: IPC request overhead in Python < 3ms.
- **Completion Criteria**: Python can query `tom-engine` for system status, receive responses, and handle disconnections gracefully.
- **Exit Criteria**: Bi-directional communication validated; zero unhandled connection exceptions.
- **Potential Risks**: Named pipe blocking on Windows asyncio event loop.
- **Rollback / Recovery**: Fall back to localhost TCP loopback if named pipe asyncio driver has stability issues.

---

### Phase 3 — Deterministic Tools & Permission Engine

- **Objective**: Implement the deterministic tool system, tool registry, execution engine with timeouts and cancellation, Pydantic input/output schemas, and the 3-tier permission model (`SAFE`, `ASK USER`, `BLOCK`).
- **Prerequisites**: Phase 2 complete.
- **Components**:
  - `python/tom/tools/registry.py` (Discovery, cataloging, schema generation)
  - `python/tom/tools/executor.py` (Validation, permission enforcement, execution, timeout)
  - `python/tom/security/permissions.py` (SAFE / ASK / BLOCK evaluation)
  - `python/tom/security/confirmation.py` (User confirmation hook / callback interface)
  - `python/tom/tools/system.py` (Hardware, processes, power)
  - `python/tom/tools/files.py` (Sandboxed file operations)
- **Skills**: `python/tool-system`, `security/permission-model`, `agent-development/tool-design`, `coding/validation`.
- **Dependencies**: `pydantic`, `psutil`.
- **Implementation Tasks**:
  1. Build `ToolRegistry` with `@tool` decorator extracting Pydantic schema and permission classification.
  2. Implement `PermissionManager` evaluating action safety against policy rules.
  3. Implement `ToolExecutor` enforcing: schema validation → permission check → timeout wrapping → error normalization.
  4. Implement baseline System tools (CPU, RAM, GPU stats, running processes).
  5. Implement baseline File tools (read file, list directory, search files) with path-traversal guards.
- **Testing**:
  - Unit tests for permission classification (`SAFE` executes, `ASK` triggers hook, `BLOCK` raises error).
  - Unit tests for directory traversal prevention (`../../Windows/System32` blocked).
  - Timeout enforcement tests (hanging tool canceled after deadline).
- **Security Considerations**: No raw shell execution; paths must be resolved and checked against whitelist; dangerous operations blocked by default.
- **Performance Considerations**: Tool dispatch overhead < 1ms.
- **Completion Criteria**: Tool registry self-describes tools for LLM consumption; unauthorized/dangerous tools reliably blocked; system/file tools tested.
- **Exit Criteria**: All security boundary unit tests pass.
- **Potential Risks**: False positives in permission classification blocking user workflows.
- **Rollback / Recovery**: Maintain default permissive dry-run mode for development verification.

---

### Phase 4 — Local LLM & Model Infrastructure

- **Objective**: Build the local LLM interface and provider abstraction, supporting Qwen3-8B reasoning model via local execution (llama-cpp-python / vLLM) with optional cloud fallback.
- **Prerequisites**: Phase 0-2 complete.
- **Components**:
  - `python/tom/models/base.py` (Common model adapter interface)
  - `python/tom/models/reasoning.py` (Qwen3-8B adapter with streaming support)
  - `python/tom/models/providers/local.py` (llama-cpp-python / GGUF local runtime)
  - `python/tom/models/providers/cloud.py` (OpenAI-compatible cloud API fallback)
- **Skills**: `system-design/resource-management`, `coding/structured-logging`.
- **Dependencies**: `llama-cpp-python` (with CUDA), `httpx`, `pydantic`.
- **Implementation Tasks**:
  1. Define standard `LLMProvider` protocol (generate, stream, tokenize, count_tokens).
  2. Implement local GGUF provider loading quantized Qwen3-8B (Q4_K_M / Q5_K_M targeting ~5.5GB VRAM).
  3. Implement cloud API provider (OpenAI/Anthropic compatible) with API key redaction.
  4. Build mock provider for fast local test suites without GPU requirements.
- **Testing**:
  - Unit tests with mock provider verifying prompt formatting, token streaming, and exception propagation.
  - Benchmarking script measuring tokens per second (target > 25 tok/s on RTX 4060).
- **Security Considerations**: API keys loaded via environment variables; never logged in telemetry or error dumps.
- **Performance Considerations**: Model loading time monitoring; context window limits (8k default).
- **Completion Criteria**: Local model produces coherent reasoning responses and tool call specifications; streaming tokens yield smoothly.
- **Exit Criteria**: Benchmark confirms < 6GB VRAM footprint for reasoning LLM.
- **Potential Risks**: CUDA compilation issues for llama-cpp-python on Windows.
- **Rollback / Recovery**: Use CPU quantized inference or cloud provider fallback during development.

---

### Phase 5 — Router & Task Lifecycle Engine

- **Objective**: Implement the Intent Router and Model Router (using lightweight Qwen3-1.7B or deterministic rule heuristics), Task State Machine (`PENDING`, `PLANNING`, `RUNNING`, `WAITING_CONFIRMATION`, `COMPLETED`, `FAILED`, `CANCELLED`), and cancellation infrastructure.
- **Prerequisites**: Phase 3 and Phase 4 complete.
- **Components**:
  - `python/tom/schemas/routing.py` (Routing decision schema)
  - `python/tom/models/router.py` (Qwen3-1.7B router adapter)
  - `python/tom/core/router.py` (Intent classification & model selection)
  - `python/tom/core/context.py` (Task context, cancellation tokens)
  - `python/tom/schemas/tasks.py` (Task state schemas)
- **Skills**: `agent-development/task-lifecycle`, `coding/validation`, `python/pydantic-agents`.
- **Dependencies**: `pydantic`, `asyncio`.
- **Implementation Tasks**:
  1. Define `IntentType` (`CONVERSATION`, `SYSTEM_COMMAND`, `FILE_OPERATION`, `RESEARCH`, `VISION_QUERY`, etc.).
  2. Implement fast router prompt returning structured JSON routing schema.
  3. Implement `TaskState` lifecycle manager tracking transitions and publishing state events.
  4. Implement cooperative cancellation using `asyncio.Event` tokens propagated to tool and model invocations.
- **Testing**:
  - Route classification accuracy tests across representative prompt sets.
  - Cancellation tests verifying immediate termination of long-running operations.
- **Security Considerations**: Reject prompts attempting to bypass routing or inject prompt escapes into routing context.
- **Performance Considerations**: Router latency budget < 350ms for local Qwen3-1.7B.
- **Completion Criteria**: Requests cleanly classified; tasks transition through proper lifecycle states; cancellation triggers clean cleanup.
- **Exit Criteria**: Task state machine unit tests pass 100%.
- **Potential Risks**: Misclassification of complex multi-intent user requests.
- **Rollback / Recovery**: Default to main reasoning model when router confidence is low.

---

### Phase 6 — Agents & Planning System

- **Objective**: Integrate Pydantic AI for structured reasoning, create the primary Assistant Agent, multi-step Task Agent, planner, and error recovery engine.
- **Prerequisites**: Phases 3, 4, and 5 complete.
- **Components**:
  - `python/tom/core/orchestrator.py` (Main loop coordinate input → router → agent → execution)
  - `python/tom/core/planner.py` (Decomposition of complex requests into sub-tasks)
  - `python/tom/agents/assistant.py` (Primary interaction agent)
  - `python/tom/agents/task_agent.py` (Autonomous multi-step execution agent)
  - `python/tom/agents/dependencies.py` (Typed runtime dependency container)
- **Skills**: `python/pydantic-agents`, `agent-development/task-lifecycle`, `agent-development/tool-design`, `debugging/python-agent`.
- **Dependencies**: `pydantic-ai`, `pydantic`.
- **Implementation Tasks**:
  1. Define typed dependencies (`TOMDeps`) providing access to tool executor, memory, and engine IPC.
  2. Implement primary Assistant Agent equipped with conversational and quick-action tools.
  3. Implement Task Agent with step-by-step planning and intermediate self-reflection.
  4. Add automated retry and error recovery for recoverable tool failures (max 3 retries).
- **Testing**:
  - End-to-end unit tests with mock LLM verifying multi-step plan generation and tool dispatch.
  - Failure recovery tests verifying agent adapts when a tool returns an error.
- **Security Considerations**: Agent permissions strictly bounded by caller context; autonomous loops capped at max iterations (default 10).
- **Performance Considerations**: Context window compression to avoid prompt bloat across multi-step execution.
- **Completion Criteria**: TOM handles single-turn queries and multi-step tasks; tool outputs properly synthesized into final answers.
- **Exit Criteria**: Multi-step plan execution validated against simulated file and system tasks.
- **Potential Risks**: Agent infinite looping or hallucinating non-existent tools.
- **Rollback / Recovery**: Hard iteration counter limits and strict tool schema validation.

---

### Phase 7 — Memory Architecture

- **Objective**: Implement the authoritative SQLite structured store and Qdrant semantic vector index, unified behind `MemoryManager` with strict memory policies and user control.
- **Prerequisites**: Phase 2 and Phase 3 complete.
- **Components**:
  - `python/tom/memory/manager.py` (Single public entry point)
  - `python/tom/memory/sqlite.py` (Authoritative relational store)
  - `python/tom/memory/qdrant.py` (Semantic vector retrieval)
  - `python/tom/memory/embeddings.py` (Local embedding model adapter)
  - `python/tom/memory/policies.py` (What to remember vs exclude)
  - `python/tom/tools/memory.py` (Agent tools: remember, recall, forget)
- **Skills**: `python/memory-system`, `security/permission-model`, `coding/validation`.
- **Dependencies**: `sqlite3`, `qdrant-client`, `fastembed` or `sentence-transformers`.
- **Implementation Tasks**:
  1. Create SQLite schema for conversations, entity facts, user preferences, and task histories.
  2. Implement `SQLiteMemory` with transactional integrity and migrations.
  3. Implement `QdrantMemory` for vector indexing and semantic top-k search.
  4. Implement `MemoryManager` coordinating writes (SQLite first, then vector index) and hybrid searches.
  5. Enforce memory policy: never store passwords, tokens, ephemeral system states, or sensitive PII unless confirmed.
  6. Provide explicit tools for user-requested forgetting / memory inspection.
- **Testing**:
  - Unit tests for SQLite CRUD operations and transaction rollback.
  - Hybrid search retrieval ranking tests.
  - Security policy tests: verify credential strings are filtered prior to storage.
- **Security Considerations**: Memory database encrypted or restricted to user filesystem permissions; secrets strictly blocked.
- **Performance Considerations**: Fast SQLite lookups (< 5ms); vector search latency < 25ms; embedding generation cached.
- **Completion Criteria**: Facts remembered persist across restarts; semantic search retrieves relevant context; forgetting deletes from both SQLite and Qdrant.
- **Exit Criteria**: Memory manager test suite passes 100%.
- **Potential Risks**: Qdrant memory overhead or embedding model VRAM contention.
- **Rollback / Recovery**: Run embedding model on CPU via `fastembed` to reserve GPU VRAM.

---

### Phase 8 — Voice Pipeline

- **Objective**: Build the real-time audio pipeline: wake-word detection in Rust/openWakeWord, Silero VAD, Whisper STT (CPU vs GPU benchmarked), Speech Formatter, Kokoro-82M TTS, and voice interruption.
- **Prerequisites**: Phase 1 (Rust audio capture), Phase 4 (LLM), Phase 5 (cancellation).
- **Components**:
  - `rust/tom-engine/src/audio/` (Audio capture ring buffer, playback stream)
  - `rust/tom-engine/src/wakeword/` (openWakeWord inference or IPC stream)
  - `python/tom/voice/pipeline.py` (Orchestrator for voice loop)
  - `python/tom/voice/stt.py` (Whisper Large-v3-Turbo / faster-whisper)
  - `python/tom/voice/tts.py` (Kokoro-82M streaming audio generation)
  - `python/tom/voice/formatter.py` (Sanitizing markdown/code into speakable prose)
- **Skills**: `rust/async-tokio`, `rust/ipc`, `system-design/resource-management`, `agent-development/task-lifecycle`.
- **Dependencies**: `faster-whisper`, `kokoro-onnx` / `sounddevice`, `numpy`.
- **Implementation Tasks**:
  1. Implement audio capture in Rust streaming 16kHz mono PCM over IPC or shared buffer.
  2. Implement wake-word detector ("Hey Tom") triggering state transition to `LISTENING`.
  3. Integrate VAD to detect user speech completion.
  4. Integrate Whisper STT with benchmarked CPU (OpenVINO) vs GPU execution.
  5. Implement speech formatter converting markdown tables, bullets, and code blocks into conversational speech.
  6. Implement Kokoro-82M TTS with streaming playback.
  7. Implement speech barge-in (interruption): user speech during TTS immediately cancels audio playback.
- **Testing**:
  - End-to-end voice latency measurement (VAD end of speech to first TTS audio chunk: target < 1200ms).
  - Barge-in cancellation responsiveness test (playback stops < 100ms after new speech detected).
- **Security Considerations**: Microphone stream strictly processed locally; no audio frames sent to cloud.
- **Performance Considerations**: Whisper VRAM/CPU balance; Kokoro-82M requires < 300MB RAM/VRAM.
- **Completion Criteria**: Speaking wake word activates assistant; speech transcribes accurately; formatted response speaks aloud with low latency; interruption works.
- **Exit Criteria**: Latency benchmarks meet acceptable interactive threshold (< 1.5s total turnaround).
- **Potential Risks**: Audio driver conflicts on Windows; acoustic echo triggering false barge-in.
- **Rollback / Recovery**: Fall back to push-to-talk hotkey mode if wake-word false positives are high.

---

### Phase 9 — Vision System

- **Objective**: Implement screen capture, fast computer vision (OpenCV / UI element detection), and Vision-Language Model (VLM) integration with tier selection (fast, primary, deep).
- **Prerequisites**: Phase 3 (Tools), Phase 4 (Models), Phase 5 (Router).
- **Components**:
  - `python/tom/vision/pipeline.py` (Screen capture and preprocessing)
  - `python/tom/vision/manager.py` (VLM tier selection: Fast local, Primary local, Deep cloud)
  - `python/tom/vision/vlm.py` (Qwen2.5-VL / Moondream adapter)
  - `python/tom/vision/ocr.py` (Fast local OCR via Tesseract / RapidOCR)
  - `python/tom/tools/computer.py` (Screenshot & UI inspection tools)
- **Skills**: `system-design/resource-management`, `python/pydantic-agents`, `agent-development/tool-design`.
- **Dependencies**: `opencv-python`, `pillow`, `screeninfo`, VLM runtime or cloud API.
- **Implementation Tasks**:
  1. Implement high-speed screen capture supporting multi-monitor selection.
  2. Build OCR and classical CV contour detection for lightweight non-LLM UI analysis.
  3. Implement `VisionManager` managing VLM lifecycle (loading on demand, evicting when memory pressured).
  4. Provide agent tools for inspecting current screen, locating buttons, and reading text.
- **Testing**:
  - Screen capture latency test (< 50ms for 1080p).
  - OCR extraction accuracy on sample desktop windows.
  - VLM VRAM allocation test: ensure VLM load does not cause OOM with reasoning model.
- **Security Considerations**: Screenshots may capture banking/password windows; exclude sensitive windows; do not persist screenshots to disk permanently.
- **Performance Considerations**: VLM load time; dynamic resizing of images prior to inference.
- **Completion Criteria**: Agent can answer questions about content on screen; OCR reliably extracts text.
- **Exit Criteria**: Screen analysis tools integrated with permission manager.
- **Potential Risks**: 8GB VRAM limit exceeded if 8B LLM and VLM are co-loaded.
- **Rollback / Recovery**: Evict reasoning model to RAM before loading VLM, or route vision queries to cloud VLM.

---

### Phase 10 — Web & Personal Productivity Tools

- **Objective**: Implement secure web searching, page retrieval, browser automation, and personal organization tools (calendar, notes, reminders).
- **Prerequisites**: Phase 3 (Tools), Phase 6 (Agents), Phase 7 (Memory).
- **Components**:
  - `python/tom/tools/web.py` (DuckDuckGo / SearXNG search, readability extractor)
  - `python/tom/tools/personal.py` (Reminders, notes, local calendar)
  - `python/tom/tools/ai.py` (Image generation, summarization)
- **Skills**: `python/tool-system`, `security/permission-model`, `coding/validation`.
- **Dependencies**: `httpx`, `beautifulsoup4`, `trafilatura`.
- **Implementation Tasks**:
  1. Implement privacy-preserving web search without API keys (via DuckDuckGo / SearXNG).
  2. Implement clean markdown webpage extraction stripping ads and navigation.
  3. Implement local SQLite-backed reminders and calendar events with trigger scheduler.
  4. Register all tools with appropriate permission classifications.
- **Testing**:
  - Web search and scraping integration tests with timeout/error handling.
  - Reminder notification scheduling and firing tests.
- **Security Considerations**: Block SSRF (disallow scraping `127.0.0.1`, `192.168.*`, `10.*`); sanitize HTML.
- **Performance Considerations**: Scraper timeout < 5s; content truncated to fit LLM context.
- **Completion Criteria**: TOM searches the web, summarizes articles, and manages local reminders.
- **Exit Criteria**: Web and personal tools covered by unit tests.
- **Potential Risks**: Web pages blocking scrapers or returning anti-bot captchas.
- **Rollback / Recovery**: Return graceful error to agent to seek alternate sources.

---

### Phase 11 — Security Hardening & Isolation

- **Objective**: Comprehensive security review and hardening: path traversal penetration tests, automated secret scrubbing in all outputs, strict permission confirmation dialogues, and tool capability sandboxing.
- **Prerequisites**: Phases 0-10 implemented.
- **Components**:
  - `python/tom/security/policies.py` (Hardened path & execution rules)
  - `python/tom/security/secrets.py` (Entropy and regex secret scanner)
  - `python/tom/security/confirmation.py` (Console/GUI interactive approval)
  - `tests/security/` (Dedicated security test harness)
- **Skills**: `security/permission-model`, `security/secrets`, `coding/validation`.
- **Dependencies**: `pydantic`, `cryptography`.
- **Implementation Tasks**:
  1. Implement strict path jail ensuring file tools cannot escape configured safe zones without explicit `ASK USER` confirmation.
  2. Implement entropy-based and pattern-based secret scrubbers across logs, tool arguments, and LLM context.
  3. Build interactive confirmation flow for all `ASK USER` classified actions.
  4. Create penetration test suite testing prompt injection, path traversal, command injection, and secret leakage.
- **Testing**:
  - Run adversarial test suite (`tests/security/test_traversal.py`, `tests/security/test_injection.py`, `tests/security/test_secrets.py`).
- **Security Considerations**: Zero tolerance for unauthorized execution or unredacted secrets.
- **Performance Considerations**: Regex/entropy scanners must add < 2ms latency to request pipeline.
- **Completion Criteria**: All penetration tests pass; confirmation flow cannot be bypassed by prompt injection.
- **Exit Criteria**: 100% security test suite passing; documented security audit.
- **Potential Risks**: Overly aggressive secret redactor masking legitimate user data.
- **Rollback / Recovery**: Tune regex patterns to balance false positives.

---

### Phase 12 — Concurrency & Resource Management

- **Objective**: Implement the Resource & Model Lifecycle Manager enforcing the 8GB RTX 4060 VRAM budget, GPU contention locks, dynamic model eviction/residency, and end-to-end streaming pipeline synchronization.
- **Prerequisites**: Phases 4, 8, 9 complete.
- **Components**:
  - `python/tom/resources/manager.py` (VRAM/RAM resource tracker)
  - `python/tom/resources/model_manager.py` (Load/unload/warmup coordinator)
  - `python/tom/resources/policies.py` (Eviction priorities: Router > LLM > VLM > Whisper)
- **Skills**: `system-design/resource-management`, `agent-development/task-lifecycle`.
- **Dependencies**: `pynvml` or `torch.cuda`, `psutil`.
- **Implementation Tasks**:
  1. Implement VRAM tracker querying NVIDIA NVML in real time.
  2. Implement mutex/lock for GPU model execution preventing VRAM oversubscription.
  3. Build model residency manager: automatically evict VLM after visual inspection to free VRAM for Qwen3-8B.
  4. Benchmark and lock Whisper placement (CPU vs GPU) based on concurrent load.
  5. Synchronize streaming audio pipeline under high GPU contention.
- **Testing**:
  - Stress test: concurrent voice STT + LLM generation + screen capture + TTS. Verify no CUDA OOM crashes.
- **Security Considerations**: Prevent denial of service from excessive task queuing.
- **Performance Considerations**: Total VRAM usage strictly capped at 7.2GB (leaving 0.8GB for OS/display).
- **Completion Criteria**: Zero CUDA Out-Of-Memory crashes under heavy load; models swapped gracefully when needed.
- **Exit Criteria**: Stress test runs 50 continuous cycles without memory leak or OOM.
- **Potential Risks**: Model swapping latency causing noticeable pause in response.
- **Rollback / Recovery**: Prioritize CPU offload for Whisper and TTS to keep reasoning LLM resident.

---

### Phase 13 — Observability, Metrics & TOM Monitor

- **Objective**: Build telemetry instrumentation, latency tracking across all pipeline hops, metrics aggregation, and the local TOM Monitor dashboard.
- **Prerequisites**: Phases 1-12 complete.
- **Components**:
  - `python/tom/telemetry/metrics.py` (In-memory latency & token counter metrics)
  - `python/tom/telemetry/tracing.py` (End-to-end request tracing)
  - `scripts/health_check.py` (Diagnostic CLI utility)
  - Local monitoring web UI or CLI dashboard
- **Skills**: `coding/structured-logging`, `testing/strategy`.
- **Dependencies**: `structlog`, `rich` / `fastapi`.
- **Implementation Tasks**:
  1. Record timing metrics at every stage: Wake-word → STT → Router → LLM TTFT → TTS → Playback.
  2. Implement diagnostic health check verifying IPC connection, model availability, and VRAM status.
  3. Implement terminal dashboard or lightweight web interface showing real-time TOM vital signs.
- **Testing**:
  - Verify metrics exporter correctly logs latency percentiles (p50, p95, p99).
- **Security Considerations**: Telemetry dashboard restricted to local loopback; sensitive queries masked.
- **Performance Considerations**: Telemetry recording overhead < 0.1ms per event.
- **Completion Criteria**: Complete breakdown of request latencies visible; bottlenecks identifiable in logs.
- **Exit Criteria**: Diagnostic health check script passes all green.
- **Potential Risks**: Heavy metric recording generating large log files on disk.
- **Rollback / Recovery**: Automatic log rotation and retention policies (max 7 days / 100MB).

---

### Phase 14 — Personality, UX & Full Integration

- **Objective**: Final prompt tuning, speech styling, persona consistency (concise, competent, Jarvis-like), CLI/desktop system tray integration, and end-to-end system testing.
- **Prerequisites**: All prior phases complete.
- **Components**:
  - `python/tom/main.py` (Unified entry point)
  - `config/tom.yaml` (Production configuration presets)
  - System tray icon / background daemon launcher
  - End-to-end acceptance test suite
- **Skills**: `testing/strategy`, `agent-development/task-lifecycle`.
- **Dependencies**: Full stack.
- **Implementation Tasks**:
  1. Tune system prompts for concise, direct responses suitable for voice output.
  2. Implement desktop system tray icon with quick status and mute toggle.
  3. Build comprehensive end-to-end automated integration tests.
  4. Document operational guide in `README.md` and `docs/`.
- **Testing**:
  - Complete user scenario walkthroughs: voice interaction, file management, computer inspection, error recovery.
- **Security Considerations**: Production release mode with debug endpoints disabled.
- **Performance Considerations**: Idle resource consumption < 1% CPU, < 200MB RAM when models are cold.
- **Completion Criteria**: TOM runs reliably as an everyday background operating assistant.
- **Exit Criteria**: Project state signed off; all verification tests pass.
- **Potential Risks**: User experience edge cases.
- **Rollback / Recovery**: Graceful fallback to text CLI if audio subsystems fail.

---

## 4. Open Architectural Questions

1. **Rust Toolchain Availability**: The current development environment lacks `cargo` / `rustc` on PATH. Should Phase 1 development proceed by compiling `tom-engine` after installing Rust via `rustup`, or should a Python mock IPC server be maintained in parallel to allow full Python stack testing before Rust compilation?
   - *Working Decision*: Maintain a high-fidelity Python mock of the named-pipe IPC server in `tests/mocks/mock_engine.py` so Phase 2-6 development is never blocked by native toolchain setup.
2. **Local STT Placement (CPU vs GPU)**: Whisper Large-v3-Turbo on CUDA consumes ~1.5GB VRAM, competing with Qwen3-8B (5.5GB).
   - *Working Decision*: In Phase 0-4, configure Whisper for CPU execution via `faster-whisper` (utilizing AVX2/AVX-512) and evaluate latency. Migrate to GPU only if CPU latency exceeds 500ms for a 3-second utterance.
3. **IPC Transport on Windows**: plan.md suggests Named Pipes or local sockets.
   - *Working Decision*: Implement Windows Named Pipes (`\\.\pipe\tom-engine`) as the primary transport for security and zero port contention, with an abstract transport interface allowing localhost TCP fallback.
