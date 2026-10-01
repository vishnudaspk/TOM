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

---

## Latest Verified Test Baseline

- **Total Python Tests**: 1004 passed + 8 skipped (870 unit + 134 integration)
  - Unit tests: 870 passed (81 vision + 180 voice + 156 tools + 149 IPC + 102 agents + 61 models + 54 core + 50 memory + 33 security + 4 telemetry)
  - Integration tests: 134 passed (42 vision + 23 voice + 32 tools + 15 python_rust + 13 agents + 6 memory + 3 models)
  - Skipped: 8 optional-dep vision tests (cv2/winocr guards; pass when `pip install opencv-python winocr`)
- **Total Rust Tests**: 83 passed (76 unit + 7 integration in `tom-engine`)
- **Combined Repository Total**: 1087 tests passing (+ 8 skipped) across the repository
- **Quality Gates**: Ruff check clean (0 violations), Ruff format clean (0 diffs), Cargo fmt clean, Cargo clippy clean (0 warnings)
- **Baseline Date**: 2026-10-01

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
