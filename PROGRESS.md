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
| **Phase 7** | Vision & Multimodal Capabilities / Extended OS Automation | CLOSED | 1087 passed (1004 Py + 83 Rust) | 2026-10-01 |
| **Phase 8** | Autonomous Proactive Agent & Long-Horizon Execution | CLOSED | 1301 passed (1218 Py + 83 Rust) | 2026-10-02 |

---

## Latest Verified Test Baseline

- **Total Python Tests**: 1218 passed + 8 skipped (1048 unit + 170 integration)
  - Unit tests: 1048 passed (198 Phase 8 agents/resources/models [+65 proactive, +14 resource manager, +6 benchmark suite, +11 executor, +7 revalidator, +20 planner, +14 loop detector, +41 task manager, +20 core agent loop] + 81 vision + 180 voice + 156 tools + 149 IPC + 61 models + 54 core + 50 memory + 33 security + 4 telemetry)
  - Integration tests: 170 passed (49 agents [+15 long horizon, +10 confirmation, +11 cancellation, +13 agent pipeline] + 42 vision + 32 tools + 23 voice + 15 python_rust + 6 memory + 3 models)
  - Skipped: 8 optional-dep vision tests (cv2/winocr guards; pass when `pip install opencv-python winocr`)
- **Total Rust Tests**: 83 passed (76 unit + 7 integration in `tom-engine`)
- **Combined Repository Total**: 1301 tests passing (+ 8 skipped) across the repository
- **Quality Gates**: Ruff check clean (0 violations), Ruff format clean (0 diffs), Cargo fmt clean, Cargo clippy clean (0 warnings)
- **Baseline Date**: 2026-10-02

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

### Phase 7: Vision & Multimodal Capabilities / Extended OS Automation (CLOSED & COMPLETE)
- **Objective**: Implement local vision perception (screen capture, OCR, CV contour detection, local VLM) and controlled OS input actuation (mouse/keyboard primitives) with strict privacy boundaries and zero disk persistence.
- **Iteration 0: Rust Input IPC & OS Input Boundary**:
  - `InputManager` orchestrating canonical virtual-desktop coordinate clamping, emergency cancellation hotkey (`Ctrl+Alt+Shift+Escape`), corner slam fail-safe, and parameter bounding.
  - Win32 `SendInput` primitives in Rust engine.
  - Low-level `input.*` IPC handlers exposed over Named Pipe.
- **Iteration 1: Screen Capture & Privacy**:
  - `python/tom/schemas/vision.py`: `MonitorInfo`, `ScreenDimensions`, `CapturedFrame` (with `raw_bytes` excluded from dumps and string representations), `WindowInfo`, and `PrivacyCheckResult`.
  - `python/tom/vision/privacy.py`: `PrivacyShield` enforcing sensitive window title pattern filtering (`*password*`, `*bitwarden*`, `*1password*`, `*keepass*`, `*bank*`, `*private browsing*`, `*incognito*`), regex redaction of credentials/PII (credit cards, SSNs, API keys/tokens), ephemeral in-memory lifecycle enforcement, and log sanitization.
  - `python/tom/vision/capture.py`: `ScreenCaptureService` with multi-monitor discovery, DPI awareness and coordinate conversion (DPI-scaled to canonical virtual desktop), region cropping, and in-memory capture using `mss` with Pillow fallback. Zero disk persistence.
  - Configuration: `VisionConfig` added to `TOMConfig` and default `config/vision.yaml`.
- **Iteration 2: OCR & Fast Computer Vision**:
  - `python/tom/schemas/vision.py`: Extended with `BoundingBox`, `Point2D`, `TextLocation`, `OCRResult`, `ElementType` (StrEnum), `UIElement`.
  - `python/tom/vision/ocr.py`: `OCRProvider` ABC, `MockOCRProvider` (deterministic test double), `WindowsMediaOCRProvider` (lazy-loaded Windows.Media.Ocr via `winocr`, CPU-only, 0 VRAM), `OCRError`/`OCRProviderUnavailableError`. Production raises explicitly if `winocr` absent; never silently falls back to mock. All OCR text piped through `PrivacyShield.filter_extracted_text()`.
  - `python/tom/vision/cv.py`: `CVElementDetector` using OpenCV Canny edge detection and contour analysis. Classifies UI elements (`BUTTON`, `INPUT_FIELD`, `TEXT_BLOCK`, `ICON`, `CONTAINER`) by aspect ratio and area heuristics. `ElementDetectorHook` seam for future YOLO/SAM extension. Pure CPU, 0 VRAM.
  - Optional deps `winocr` and `numpy>=1.24.0` added to `vision` optional group in `pyproject.toml`.
- **Iteration 3: VLM Abstraction & VisionManager**:
  - `python/tom/schemas/vision.py`: Extended with `VisionCapability` (`OCR_ONLY`, `ELEMENT_LOCATION`, `FAST_VLM`, `PRIMARY_VLM`, `DEEP_VLM`), `VLMRequest`, `VLMResponse`, `VisionAnalysisRequest`, `VisionAnalysisResult`, and `scale()` on `BoundingBox`.
  - `python/tom/vision/vlm.py`: `VLMProvider` ABC, `MockVLMProvider` (deterministic offline test double), `LocalVLMProvider` (OpenAI-compatible `/v1/chat/completions` multimodal adapter using base64 data URIs). Strict local-only enforcement (`CloudVLMRejectedError` for non-local hosts). Dynamic coordinate re-scaling mapping downscaled VLM coordinates (max 1280px) back to canonical screen coordinates. Strong error hierarchy (`VLMError`, `VLMConnectionError`, `VLMTimeoutError`, `VLMResponseError`).
  - `python/tom/vision/manager.py`: `VisionManager` central perception coordinator. Capability-driven routing (cheapest sufficient first: OCR-only -> OCR+CV -> Local VLM). GPU VRAM telemetry seam integration checking memory pressure (< 1200MB free or > 7000MB used) before VLM inference. Convenience APIs (`extract_text`, `find_element`, `ask`). Ephemeral RAM lifecycle with zero disk persistence.
- **Iteration 4: Vision/Input Tools & Agent Wiring**:
  - `python/tom/tools/vision.py`: 4 tools (`vision.capture`, `vision.ocr`, `vision.find_element`, `vision.ask`), all `SAFE` tier; delegate to `VisionManager`; zero duplicated vision logic.
  - `python/tom/tools/input.py`: 4 tools (`os.input.click`, `os.input.type_text`, `os.input.hotkey`, `os.input.get_cursor_pos`); first three `ASK_USER` tier routing input primitives through `EngineClient` IPC to Rust `SendInput`; `get_cursor_pos` is `SAFE`.
  - `python/tom/security/permissions.py`: Added `DEFAULT_TOOL_PERMISSIONS` dict (all 28 tools explicitly registered). `PermissionEngine.evaluate` now uses data-driven lookup exclusively — string-prefix matching fully removed.
  - `python/tom/agents/dependencies.py`: Added optional `vision_manager: object | None = Field(default=None)` to `AgentDependencies`; backward-compatible default.
  - `python/tom/tools/bootstrap.py`: `setup_default_tools` now registers all 28 built-in tools (was 20). Both legacy `ToolBootstrapResult` and current return forms supported.
  - Tests: `tests/unit/tools/test_vision_tools.py` and `tests/unit/tools/test_input_tools.py` added; all mock/dry-run, zero physical mouse/keyboard actions.
- **Iteration 5: Integration, Benchmarks & Phase 7 Exit Gate**:
  - `tests/integration/vision/test_vision_pipeline.py`: Comprehensive end-to-end vision pipeline integration tests (screen capture -> privacy filtering -> OCR -> CV -> VLM -> VisionManager). Verified capability routing, coordinate mapping, privacy filtering, and error resilience without disk persistence or cloud connectivity.
  - `tests/integration/vision/test_input_pipeline.py`: Comprehensive end-to-end input pipeline integration tests verifying `os.input.click`, `os.input.type_text`, `os.input.hotkey` (`ASK_USER` tier requiring user confirmation, unconfirmed action rejection, 500-char type limits, coordinate bounding) and `os.input.get_cursor_pos` (`SAFE` tier) through `ToolExecutor` and `PermissionEngine` to mocked/dry-run Rust boundary. Audited AST to guarantee Python does not directly import `pyautogui`, `pywin32`, or `pynput`.
  - `tests/integration/vision/test_agent_vision_integration.py`: End-to-end agent workflow integration tests covering `vision.capture` and `vision.ask` using `AgentDependencies.vision_manager`, confirming backward compatibility and non-bypassable tool execution.
  - `tests/benchmarks/vision/`: Created latency benchmarks (`bench_capture.py`, `bench_ocr.py`, `bench_cv.py`). Measured capture and OCR performance against target SLAs without cloud or disk persistence.
  - Exit Gate: 1004 Python tests (870 unit + 134 integration) + 83 Rust tests = 1087 total passing (+ 8 skipped optional-dep tests). Ruff check clean, Ruff format clean, Cargo fmt clean, Cargo clippy clean.
- **Status**: CLOSED & COMPLETE.

---

## Architectural Decisions Summary (Phase 7)

- **Decision 046: Screen Capture & Ephemeral Privacy Boundary**  
  Screen capture runs in Python via `ScreenCaptureService` using `mss` (Pillow fallback) and DPI coordinate normalization to the virtual desktop. Images exist ephemerally in RAM only (`CapturedFrame.raw_bytes` excluded from serialization and logs). `PrivacyShield` intercepts sensitive windows (e.g., password managers, banking, private browsing) and redacts PII/credentials before perception processing. Zero disk persistence.
- **Decision 047: Pure CPU OCR Isolation via Windows.Media.Ocr**  
  `OCRProvider` abstracts text recognition with `WindowsMediaOCRProvider` (via `winocr`) and deterministic `MockOCRProvider`. OCR operates strictly on CPU with 0 VRAM impact, preserving GPU resources for the primary LLM. All extracted text is filtered through `PrivacyShield.filter_extracted_text()`.
- **Decision 048: Lightweight Fast Computer Vision via OpenCV Contours**  
  `CVElementDetector` detects interactive UI elements (`BUTTON`, `INPUT_FIELD`, `TEXT_BLOCK`, `ICON`, `CONTAINER`) using CPU-only OpenCV Canny edge and contour detection heuristics. Zero VRAM cost, running under 50 ms. Includes `ElementDetectorHook` for future pluggable neural object detection.
- **Decision 049: Capability-Driven Perception Routing & GPU Resource Protection**  
  `VisionManager` coordinates perception by evaluating the cheapest sufficient capability tier first (`OCR_ONLY` -> `ELEMENT_LOCATION` -> `FAST_VLM` / `PRIMARY_VLM`). Before dispatching to local VLMs via `LocalVLMProvider` (OpenAI-compatible `/v1/chat/completions`), GPU VRAM telemetry is checked; requests are rejected if free VRAM < 1200MB or used VRAM > 7000MB. Cloud endpoints are strictly rejected. Coordinates are dynamically scaled back to virtual desktop coordinates.
- **Decision 050: Rust OS Input Actuation & 3-Tier Security Boundary**  
  Python code never directly injects mouse or keyboard events. Actuation flows from `os.input.*` tools through `ToolExecutor` and `PermissionEngine` over Named Pipe IPC to Rust `tom-engine` `InputManager` executing Win32 `SendInput`. Input tools are assigned explicit 3-tier security (`os.input.get_cursor_pos` is `SAFE`; `click`, `type_text`, `hotkey` are `ASK_USER`). Rust guarantees coordinate clamping, emergency cancellation (`Ctrl+Alt+Shift+Escape`), and corner-slam fail-safes.

---

## Measured Software Latency Benchmarks (Phase 7 Verified)

| Benchmark Metric | Backend / Provider | Iterations | Min | Median | P95 | Max | Target | Status |
|---|---|---|---|---|---|---|---|---|
| **Screen Capture** | MockCaptureBackend | 100 | 9.256 ms | **10.099 ms** | 15.048 ms | 19.226 ms | < 30 ms | PASSED |
| **Screen Capture** | MSSCaptureBackend | — | — | — | — | — | < 30 ms | SKIPPED (BitBlt access denied in headless) |
| **OCR** | MockOCRProvider | 200 | 0.006 ms | **0.007 ms** | 0.007 ms | 0.053 ms | < 100 ms | PASSED |
| **OCR** | WindowsMediaOCRProvider | — | — | — | — | — | < 100 ms | SKIPPED / NOT MEASURED (`winocr` not installed) |
| **CV Element Detection** | CVElementDetector | — | — | — | — | — | < 50 ms | SKIPPED / NOT MEASURED (`opencv-python` not installed) |

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

---

---

## Phase 8: Autonomous Proactive Agent & Long-Horizon Execution (CLOSED)

- **Iteration 0: Task Lifecycle, Schemas & TaskManager**:
  - `python/tom/schemas/task.py`: 9-state task lifecycle (`TaskState`), Pydantic schemas (`Task`, `StepResult`, `TaskMetadata`).
  - `python/tom/agents/task_manager.py`: `TaskManager` handling creation, state transitions, step tracking, cooperative cancellation, and SQLite audit logging.
  - Verification: 41 unit tests in `tests/unit/agents/test_task_manager.py`.
- **Iteration 1: Structured TaskPlanner, Step Decomposition & Loop Detection**:
  - `python/tom/schemas/planner.py`: `Plan`, `PlanStep`, `StepDependency` Pydantic schemas.
  - `python/tom/agents/planner.py`: `TaskPlanner` with DAG validation, cycle detection, and tool schema context injection.
  - `python/tom/agents/loop_detector.py`: `LoopDetector` tracking action repetitions and cyclic failures.
  - Verification: 34 unit tests in `tests/unit/agents/test_planner.py` and `test_loop_detector.py`.
- **Iteration 2: Execution Engine, Checkpointing & Vision->Action Revalidation**:
  - `python/tom/agents/executor.py`: `TaskExecutor` coordinating DAG execution, cooperative cancellation via `CancellationToken`, step budgeting, wall-clock timeout enforcement, and SQLite checkpointing.
  - `python/tom/agents/revalidator.py`: `VisualRevalidator` pre-action safety gate querying `VisionManager.find_element`.
  - Verification: 18 unit tests in `tests/unit/agents/test_executor.py` and `test_revalidator.py`.
- **Iteration 3: Staged Model Acquisition, Benchmarking & Resource Governor**:
  - `python/tom/models/benchmark.py`: `ModelBenchmarkSuite` measuring TTFT (ms), throughput (tokens/sec), JSON schema fidelity (% valid Pydantic), and dedicated VRAM residency/peak.
  - `python/tom/resources/manager.py`: `ResourceManager` hardware governor enforcing RTX 4060 limits (8 GB VRAM, 16 GB RAM), 500 MB VRAM safety headroom margin, NVML/psutil telemetry abstraction, and mutual exclusion lock between heavy reasoning models and local VLMs.
  - `tests/benchmarks/models/bench_llm.py`: Offline benchmark scaffolding and opt-in live endpoint benchmarking harness.
  - `docs/development/RTX4060_MODEL_BENCHMARKING.md`: Architectural documentation for RTX 4060 candidate model evaluation.
  - Verification: 21 unit/benchmark tests passing (14 resource manager + 6 benchmark suite + 1 benchmark scaffolding) + 1 skipped live test. Zero regressions.
- **Iteration 4: Bounded Proactive Behavior & Event Scheduling**:
  - `python/tom/schemas/proactive.py`: `QuietHoursConfig`, `RateLimitConfig`, `CronTrigger`, `IntervalTrigger`, `SystemEventTrigger`, `ProactivePolicyDecision`. Hard safety invariants encoded in Pydantic schema.
  - `python/tom/agents/proactive.py`: `ProactiveScheduler` with 5-stage policy pipeline (enabled -> user-active -> quiet-hours -> rate-limit -> trigger-due), user preemption via `CancellationToken`, `urgent_health` bypass for critical triggers, async dispatch lock.
  - Verification: 65 unit tests in `tests/unit/agents/test_proactive.py`.
- **Iteration 5: End-to-End Integration, Scenario Tests & Phase 8 Exit Gate**:
  - `tests/integration/agents/test_long_horizon_pipeline.py`: 15 integration tests covering full stack `TaskManager -> TaskPlanner -> TaskExecutor -> ToolExecutor`, sequential and DAG plans, topological ordering, checkpoints, step failure recovery, step budget enforcement, timeout enforcement, LoopDetector halting, and permission authority.
  - `tests/integration/agents/test_confirmation_pipeline.py`: 10 integration tests verifying `ASK_USER` confirmation boundary, `WAITING_CONFIRMATION` lifecycle state, pause/resume on approval, denial halts, and PermissionEngine authority.
  - `tests/integration/agents/test_cancellation_pipeline.py`: 11 integration tests verifying cooperative cancellation via `CancellationToken` before/between/during execution, propagation across boundaries, zero orphan steps, and terminal state correctness.
  - Verification: 36 new integration tests passing (170 total integration tests across repository). Full regression clean (1218 Python + 83 Rust = 1301 passed, 8 skipped).

## Empirical Hardware Benchmarks (Phase 8 Verified — RTX 4060)

Measured empirical benchmark against local OpenAI-compatible endpoint with candidate reasoning model `qwen3-8b` on reference hardware (NVIDIA GeForce RTX 4060 Laptop GPU, 8,192 MB VRAM):

| Metric | Target SLA | Measured Value (qwen3-8b) | Status |
|---|---|---|---|
| **TTFT (Time to First Token)** | < 1,200 ms | **411.31 ms** (Min: 393.64 ms, Max: 428.84 ms, P50: 411.45 ms, P95: 428.84 ms) | PASS |
| **Generation Throughput** | > 18 tokens/sec | **25.93 tokens/sec** (Min: 25.86, Max: 26.05) | PASS |
| **JSON Schema Fidelity** | > 95.0% | **100.0%** (3/3 valid Pydantic `BenchmarkTaskSchema`) | PASS |
| **Dedicated VRAM Peak** | < 7,200 MB | **6,714.4 MB** (81.9% of 8,192 MB; 1,477.6 MB free VRAM margin) | PASS |
| **Net VRAM Residency Increase** | Minimal (< 100 MB) | **0.0 MB** net residency | PASS |

## Architectural Decisions Summary (Phase 8)

- **Decision 051: Global Task Lifecycle & Decoupled State Machines (ADR-051)**
  Decouples the high-level task lifecycle (`TaskState`: 9 states — `CREATED`, `PLANNING`, `READY`, `RUNNING`, `WAITING_CONFIRMATION`, `PAUSED`, `COMPLETED`, `FAILED`, `CANCELLED`) from low-level turn execution (`AgentState`: 6 states). `TaskManager` acts as the single source of truth for task lifecycle mutations, audit checkpoints, and state transitions, isolating long-running task orchestration from interactive conversation turns.
- **Decision 052: Structured Pydantic Plan Decomposition & Dynamic Replanning (ADR-052)**
  Enforces explicit, strongly-typed DAG and sequential plans via `Plan`, `PlanStep`, and `StepDependency` Pydantic models. Plan validation executes deterministically prior to step execution, verifying that all step IDs are unique, dependencies are non-cyclical, dependency references exist, and tools exist in `ToolRegistry`. Step failures trigger structured replanning or terminal failure without unchecked loops, governed by `LoopDetector`.
- **Decision 053: Closed-Loop Visual Target Revalidation (ADR-053)**
  Enforces a mandatory pre-actuation visual check via `VisualRevalidator` immediately prior to physical input tool execution (`os.input.click`, `os.input.type_text`, `os.input.hotkey`). Takes a fresh screen frame and re-locates target coordinates. If UI elements drift or disappear, the action is aborted before OS actuation occurs, preventing stale coordinate misclicks.
- **Decision 054: Hardware-Constrained Model Staging & Empirical Benchmarking (ADR-054)**
  Defines hardware allocation rules for the reference 8 GB GPU (RTX 4060) and 16 GB RAM. `ResourceManager` acts as a hardware governor enforcing a 500 MB safety headroom margin, NVML/psutil telemetry monitoring, and a strict mutual exclusion lock (`_heavy_lock`) between heavy reasoning LLMs (~5.2 GB) and local multimodal VLMs (~5.5 GB) to eliminate GPU Out-Of-Memory crashes. Benchmarks verify TTFT < 1,200 ms, throughput > 18 tps, and schema fidelity > 95% on local quantizations.
- **Decision 055: Bounded Proactive Scheduling & Safety Controls (ADR-055)**
  Constrains autonomous proactive actions through `ProactiveScheduler` using a 5-stage policy pipeline: global enable flag, active user preemption (user actions preempt background tasks), quiet hours window (22:00–08:00 overnight, bypassable only by `urgent_health` events), rate limits (maximum 1 task per 30 minutes), and trigger due verification. Proactive tasks proposing mutating tools must route through the `PermissionEngine` `ASK_USER` tier for explicit user confirmation.
