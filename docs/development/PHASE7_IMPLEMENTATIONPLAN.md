# TOM Phase 7 — Vision & Multimodal Capabilities / Extended OS Automation
## Implementation-Ready Architecture & Iteration Plan

**Author:** Principal AI Systems & Architecture Engineer  
**Architectural Source of Truth:** `plan.md` (Master Engineering & Architecture Plan)  
**Baseline:** Phases 0–6 CLOSED & COMPLETE.  
**Verified Tests Baseline:** 931 total passed (756 Python unit + 92 Python integration = 848 Python; 76 Rust unit + 7 Rust integration = 83 Rust) | Zero regressions.  
**Status:** PLANNING DOCUMENT — Refined & Aligned with `plan.md` and Current Repository State.  
**Target Document Path:** `docs/development/PHASE7_IMPLEMENTATIONPLAN.md`

---

## 1. Phase Objective

Implement a high-performance, privacy-preserving, local-only **Vision Perception and Extended OS Automation System** for TOM. This phase equips TOM with the ability to:
1. **Observe and perceive the desktop environment** across multi-monitor setups with zero IPC serialization overhead, native DPI scaling, and strict in-memory privacy protection (active window exclusion heuristics and regex redaction).
2. **Extract text and detect UI elements deterministically** using a lightweight CPU-first Tier 1 fast perception layer (native Windows Media OCR / `winocr` and classical OpenCV contour analysis, with extensible hooks for specialized models such as YOLO/SAM) consuming 0 VRAM and targeting < 100ms execution.
3. **Reason over visual interfaces locally** via an abstract, runtime-decoupled `VLMProvider` protocol supporting offline deterministic mocks and a generic `LocalVLMProvider` (with OpenAI-compatible multimodal adapter for local runtimes such as LM Studio or Bionic). Phase 7 runtime is strictly **LOCAL ONLY** — no cloud execution, no cloud fallback, and no screenshot transmission to external services.
4. **Select vision capabilities dynamically** through `VisionManager` using a capability-driven hierarchy rather than an unconditional cascade:
   - **Cheapest sufficient perception first**: Pure text extraction and standard button location route to CPU OCR / OpenCV without waking up a heavy VLM.
   - **Local VLM capability tiers**: When semantic understanding, scene description, or visual QA is required, route to the appropriate local VLM tier (`Fast VLM` [Ministral 3 3B candidate], `Primary VLM` [Gemma E4B candidate], or `Deep VLM` [Ministral 3 8B candidate] as defined in `plan.md`).
5. **Safely actuate OS mouse and keyboard input** via low-level primitives implemented in the Rust engine (`tom-engine`) using Windows `SendInput` over Named Pipe IPC (`\\.\pipe\tom-engine`), governed by non-bypassable, data-driven permission checks (`ASK_USER` tier), canonical virtual-desktop coordinate clamping, and multi-layer emergency fail-safes (physical corner slam detection, global cancel hotkey `Ctrl+Alt+Shift+Escape`, and parameter bounding).
6. **Expose high-level deterministic agent tools** under `vision.*` (`SAFE`) and `os.input.*` (`ASK_USER` / `SAFE`) namespaces through TOM's existing `ToolExecutor` and `PermissionEngine`.

---

## 2. Current-State Assumptions & Global Model Separation

This plan builds strictly upon the verified codebase state following the completion of Phase 6 and aligns with `plan.md`:

### Core Dual Architecture
- **Python Brain**: Coordinates intelligence, orchestration, tools, memory, and modalities.
- **Rust Engine (`tom-engine`)**: Manages low-level OS operations, telemetry, audio capture/playback, and Windows Named Pipe IPC (`\\.\pipe\tom-engine`).

### Model Architecture Separation (from `plan.md` §11, §12, §13, §15, §37)
Global routing, reasoning, and vision are separate architectural concerns:

```text
Qwen3 1.7B
    ↓
Global intent/model routing
    ↓
Specialized subsystem
    ├── tools → Rust tom-engine / Python tool executors
    ├── reasoning → Qwen3 8B (Workhorse: planning, conversation, coding, agent loop)
    └── vision → VisionManager
```

`VisionManager` has its own model-selection layer:

```text
VisionManager
      │
      ├── Fast VLM (Candidate: Ministral 3 3B)
      │
      ├── Primary VLM (Candidate: Gemma E4B)
      │
      └── Deep VLM (Candidate: Ministral 3 8B)
```

*Note: These are initial candidate/target models from `plan.md`, not permanent hard-coded dependencies. The architecture remains model-agnostic, provider-agnostic, and runtime-agnostic.*

### Model Selection vs. Resource Management Separation
Model selection and resource scheduling are decoupled:
```text
Qwen3 1.7B Router
       ↓
High-level route (e.g. VISION)
       ↓
Specialized subsystem (VisionManager)
       ↓
Model selection (selects candidate VLM tier)
       ↓
Resource Manager Seam (existing telemetry checks)
       ↓
Can the selected model run now? (evaluates VRAM budget, residency, active workloads)
       ↓
Load / reuse / unload / queue
```
- **Boundary rule**: Phase 7 must **not** implement the complete Phase 10 Resource & Model Lifecycle Manager; it consumes existing resource-management seams (e.g. `SystemMonitor` GPU/VRAM telemetry in Rust and Python).
- No hard-coded rules like "always unload the 1.7B router before loading another model."
- The small router may remain resident when hardware allows.
- Residency and concurrency decisions belong to resource management, not model logic.

### Local-Only Runtime Mandate
Phase 7 runtime is strictly **LOCAL ONLY**:
- No cloud LLM execution.
- No cloud VLM execution.
- No automatic cloud fallback.
- No screenshot transmission to cloud services.
- No cloud API requirement or dependency.
*(A future provider abstraction may leave room for cloud implementations, but such implementations are future/optional and are NOT active Phase 7 runtime paths).*

---

## 3. Why This Phase Is the Correct Next Step

1. **Natural Multimodal Evolution**: Phases 1–5 established core reasoning, memory, tools, and OS monitoring. Phase 6 added voice perception and speech interaction. Adding visual perception and OS actuation completes TOM's multimodal interface with the user and desktop.
2. **Grounding in Desktop Reality**: Agents without vision cannot perceive GUI applications that lack CLI or programmatic APIs. Vision perception enables TOM to read status messages, locate controls, and inspect application state across any Windows app.
3. **Controlled OS Actuation**: With visual perception and coordinate mapping established, safe mouse and keyboard actuation enables end-to-end task automation, transforming TOM from an advisory chatbot into a capable desktop operating agent.
4. **Reconciled Phase Sequence**: While historical master plans numbered the Vision System as Phase 9, repository milestones (`STATE.md`, `PROGRESS.md`, `HANDOFF.md`) explicitly consolidated the milestone order into **Phase 7 — Vision & Multimodal Capabilities / Extended OS Automation**.

---

## 4. Scope

### In Scope
1. **Rust Engine Input Primitives (`input.*` namespace)**:
   - Windows `SendInput` mouse actions (click, move, scroll, drag) with virtual-desktop coordinate clamping.
   - Windows `SendInput` keyboard actions (key down/up, text typing, modifier combinations).
   - Emergency cancellation hotkey (`Ctrl+Alt+Shift+Escape`) and physical corner fail-safe detection.
   - Low-level IPC handlers registered under `input.*` over Named Pipe.
   - Explicit dry-run / mock mode for non-interactive automated test execution.
2. **Python IPC Client Integration**:
   - Wire schemas in `tom.ipc.protocol`.
   - Typed client methods in `EngineClient` (`tom.core.engine`).
3. **Screen Capture & Multi-Monitor Perception**:
   - In-memory screen capture via `mss` / Pillow with zero IPC overhead.
   - Multi-monitor enumeration, resolution discovery, and DPI-to-virtual-coordinate conversion.
   - Window bounding box capture via Win32 APIs (`GetWindowRect`, `DwmGetWindowAttribute`).
4. **Privacy & Ephemeral Screenshot Lifecycle**:
   - Ephemeral in-memory lifecycle only: frames exist in RAM during request lifetime and are immediately discarded.
   - Zero screenshot persistence by default (no disk cache, no screenshot archive).
   - Zero image payloads or base64 strings in logs or telemetry.
   - Zero automatic storage of screenshots, raw OCR, or VLM outputs in SQLite or Qdrant memory (any future persistent write must pass through `MemoryManager` and `MemoryPolicy`).
   - Window title heuristic filtering (passwords, banking, private browsing) and OCR regex token redaction (credit cards, API keys) as defense-in-depth (heuristics do not guarantee detection).
5. **Tier 1 Fast Perception Layer**:
   - `OCRProvider` ABC returning structured `OCRResult` (text, confidence, bounding boxes, source region).
   - `MockOCRProvider` (offline deterministic tests) and native `WindowsMediaOCRProvider` (`winocr`) default. If real OCR is unavailable in production, raise a typed `OCRError` or route to local VLM; **never** silently substitute `MockOCRProvider` in production.
   - `CVElementDetector` using OpenCV classical contour/edge analysis for buttons, input boxes, and text regions.
   - Extensible interfaces for specialized detectors (e.g. YOLO/SAM) without requiring immediate heavy dependencies.
6. **Tier 2/3 Local VLM Provider Abstraction & VisionManager**:
   - `VLMProvider` ABC with `MockVLMProvider` and `LocalVLMProvider` (generic local provider with OpenAI-compatible multimodal adapter for local runtimes like LM Studio/Bionic).
   - Coordinate re-scaling: When images are downscaled for VLM inference (e.g. to 1280px), any detected bounding boxes or points must be explicitly un-normalized back to canonical virtual-desktop coordinates.
   - Structured schemas: `VisionAnalysisRequest`, `VisionAnalysisResult`, `BoundingBox`, `Point2D`.
   - `VisionManager` capability-driven selector (cheapest sufficient perception first; routes to Fast, Primary, or Deep local VLM only when required).
7. **Agent Tools & Data-Driven Permissions**:
   - Vision tools: `vision.capture`, `vision.ocr`, `vision.find_element`, `vision.ask` (explicitly declared as `SAFE`).
   - OS Input tools: `os.input.click`, `os.input.type_text`, `os.input.hotkey` (explicitly declared as `ASK_USER`); `os.input.get_cursor_pos` (explicitly declared as `SAFE`).
   - Tool registration in `setup_default_tools` expanding built-in tools from 20 to 28.
   - Backward-compatible dependency injection in `AgentDependencies.vision_manager: object | None = Field(default=None)`.
8. **Layer-by-Layer Benchmarks & Verification**:
   - Latency benchmarks measuring capture, privacy check, OCR, CV, VLM TTFT/inference, tool validation, permission evaluation, and IPC input round-trip.
   - Comprehensive unit and integration test suite maintaining 100% offline determinism.

### Explicit Non-Goals
1. **No Cloud LLM/VLM Execution**: Phase 7 runtime is strictly local-only. No cloud fallback, no cloud API requirement.
2. **No Continuous Video Streaming**: Discrete on-demand frame capture only; no continuous 60fps streaming or screen recording.
3. **No Unrestricted Autonomous Loops**: No unprompted infinite clicking/typing loops; every step is bounded by `AgentOrchestrator`.
4. **No Arbitrary OS Input or Shell Execution**: Input actuation is limited to tightly validated mouse and keyboard primitives.
5. **No Python-Side Input Injection**: Python must never call `pyautogui`, `pywin32`, or `pynput` directly to inject OS input; actuation is owned by Rust.
6. **No Direct LLM Access to OS Input APIs**: Model requests must flow through the agent tool pipeline, `ToolExecutor`, and `PermissionEngine`.
7. **No Persistent Screenshot Scraping or Memory Pollution**: Screenshots are never saved to disk or dumped into SQLite/Qdrant memory.
8. **No Mandatory Heavy ML Dependencies in Test Suite**: Tests must never download multi-gigabyte model weights or require external OCR binaries to pass CI.
9. **No Full Autonomous Computer Agent in Phase 7**: Phase 7 establishes perception and controlled input foundations; full autonomous computer use belongs to subsequent phases.
10. **No Production Fallback to Mocks**: `MockOCRProvider` and `MockVLMProvider` are test doubles only; production must cleanly surface errors if local engines are missing.
11. **No Implicit Elevation Bypass**: Phase 7 respects Windows UIPI (User Interface Privilege Isolation); un-elevated processes cannot interact with elevated admin windows.

---

## 5. Architecture & Data Flow

```text
User / Agent Tool Call
          |
          v
   +--------------+
   | ToolExecutor | <--- PermissionEngine (Data-driven: vision.* -> SAFE, os.input.* -> ASK_USER)
   +--------------+
          |
          +--------------------------------------------+
          | (Perception Flow: vision.*)                | (Actuation Flow: os.input.*)
          v                                            v
    vision.* tools                              os.input.* tools
          |                                            |
          v                                            v
    VisionManager                                EngineClient (Python)
(Capability-driven selection)                          | (Named Pipe IPC \\.\pipe\tom-engine)
          |                                            v
          +--------------------+               IpcDispatcher (Rust)
          |                    |                       | (Handles low-level input.* namespace)
          v                    v                       v
    Fast Perception      Local VLM Tier          InputManager (Rust)
    (0 VRAM, <100ms)    (Resource Check First)   - Canonical coordinate validation
    ├── ScreenCapture          │                 - Parameter bounds (max chars, keys)
    │   (mss / RAM)     LocalVLMProvider         - (0,0) corner slam fail-safe
    ├── OCRProvider     (LM Studio / Bionic)     - Emergency hotkey (Ctrl+Alt+Shift+Esc)
    │   (winocr/struct)        │                 - Windows SendInput API
    └── CVElementDetector      │                       |
        (OpenCV contours/      │                       v
         extensible YOLO/SAM)  │                Windows Desktop OS
          |                    |                (Mouse click, keyboard type)
          +----------+---------+
                     |
                     v
             Structured Output
          (Text, BBoxes, QA)
                     |
                     v
             RAM Discard (Ephemeral)
```

---

## 6. Files and Modules to Create or Modify

### Rust Engine (`rust/tom-engine/`)
- `src/input/mod.rs`: Update error types and expose input primitives.
- `src/input/mouse.rs`: Implement Win32 `SendInput` mouse injection, position query, and corner fail-safe.
- `src/input/keyboard.rs`: Implement Win32 `SendInput` keyboard injection, text sequence typing, and modifier synthesis.
- `src/input/manager.rs`: Create `InputManager` orchestrating canonical virtual-desktop coordinate clamping, parameter bounding, rate limiting, and emergency cancel hotkey.
- `src/ipc/handlers.rs`: Register low-level IPC handlers under `input.*`: `input.mouse_click`, `input.mouse_move`, `input.mouse_position`, `input.keyboard_press`, `input.keyboard_type`, `input.status`.
- `tests/integration_input.rs`: Rust integration tests for input handlers (using mock/dry-run mode).

### Python Brain (`python/tom/`)
- `schemas/vision.py`: Structured models: `OCRResult`, `TextLocation`, `BoundingBox`, `Point2D`, `UIElement`, `ElementType`, `MonitorInfo`, `ScreenDimensions`, `CapturedFrame`, `VisionAnalysisRequest`, `VisionAnalysisResult`.
- `schemas/ipc.py`: Wire schemas for low-level `input.*` IPC requests and responses.
- `core/engine.py`: Add typed input methods to `EngineClient` mapping high-level tool calls to low-level `input.*` IPC commands.
- `ipc/protocol.py`: Register `input.*` IPC method names.
- `vision/capture.py`: `ScreenCaptureService` (multi-monitor discovery, in-memory capture, DPI-to-virtual coordinate scaling, window rect cropping).
- `vision/privacy.py`: `PrivacyShield` (window title heuristic filtering, regex token redaction, ephemeral in-memory lifecycle enforcement).
- `vision/ocr.py`: `OCRProvider` ABC, `MockOCRProvider`, `WindowsMediaOCRProvider` (`winocr` lazy-loaded), optional fallback. Returns structured `OCRResult`. Production raises error if missing; never silently substitutes Mock.
- `vision/cv.py`: `CVElementDetector` (OpenCV contour analysis, button/field detection, extensible hooks for specialized models).
- `vision/vlm.py`: `VLMProvider` ABC, `MockVLMProvider`, `LocalVLMProvider` (generic local provider with OpenAI-compatible multimodal adapter).
- `vision/manager.py`: `VisionManager` (capability-driven selection, Fast/Primary/Deep local VLM capability hierarchy, coordination with Resource Manager telemetry).
- `tools/vision.py`: Deterministic tools: `vision.capture`, `vision.ocr`, `vision.find_element`, `vision.ask`.
- `tools/input.py`: Deterministic tools: `os.input.click`, `os.input.type_text`, `os.input.hotkey`, `os.input.get_cursor_pos`.
- `tools/bootstrap.py`: Data-driven registration of tool metadata in `ToolRegistry`.
- `agents/dependencies.py`: Add `vision_manager: object | None = Field(default=None)` to `AgentDependencies`.
- `security/permissions.py`: Data-driven permission declarations for vision and OS input tools.

### Tests
- `tests/unit/vision/test_capture.py`
- `tests/unit/vision/test_privacy.py`
- `tests/unit/vision/test_ocr.py`
- `tests/unit/vision/test_cv.py`
- `tests/unit/vision/test_vlm.py`
- `tests/unit/vision/test_manager.py`
- `tests/unit/tools/test_vision_tools.py`
- `tests/unit/tools/test_input_tools.py`
- `tests/integration/vision/test_vision_pipeline.py`
- `tests/integration/vision/test_vision_benchmarks.py`

---

## 7. Relevant Skills from `SKILL_INDEX.md`

| Iteration | Skills to Consult | Purpose |
|:---|:---|:---|
| **Iteration 0** | `rust/async-tokio`, `rust/ipc`, `rust/error-handling`, `rust/testing`, `system-design/python-rust-boundary` | Rust `SendInput` injection, emergency cancel hotkey, canonical coordinate clamping, and `input.*` IPC handlers |
| **Iteration 1** | `coding/validation`, `security/secrets`, `system-design/resource-management` | In-memory screen capture, DPI scaling, multi-monitor mapping, and ephemeral privacy filtering |
| **Iteration 2** | `system-design/resource-management`, `coding/structured-logging`, `testing/python-testing` | Structured `OCRResult` abstraction, fast classical OpenCV contour detection, and regex redaction |
| **Iteration 3** | `python/pydantic-agents`, `system-design/resource-management`, `lm-studio` | Decoupled `VLMProvider` protocol, `LocalVLMProvider`, capability-driven selection, and Resource Manager integration |
| **Iteration 4** | `agent-development/tool-design`, `python/tool-system`, `security/permission-model` | Vision & OS input tools, data-driven ToolRegistry permissions, and ToolExecutor integration |
| **Iteration 5** | `testing/strategy`, `testing/python-testing`, `documentation/project-state`, `documentation/phase-closeout` | Layered benchmarks (p50/p95), end-to-end integration tests, closeout workflow, and plan deletion |

---

## 8. Detailed Iterations

### Iteration 0: Rust Input IPC & OS Input Boundary

- **Objective**: Implement safe low-level mouse and keyboard input actuation in `tom-engine` with canonical virtual-desktop coordinate clamping, parameter bounding, emergency cancellation, and typed Named Pipe IPC exposure.
- **Implementation Work**:
  1. Create `rust/tom-engine/src/input/manager.rs`:
     - `InputManager` maintaining active monitors bounding geometry, emergency cancel atomic flag, and rate limiter.
     - Canonical coordinate system: Maps virtual desktop coordinates (handling primary, secondary, and negative monitor coordinates) and clamps every mouse movement/click to valid screen areas.
     - Win32 `SendInput` implementation for mouse moves, clicks (left, right, middle, double-click), and wheel scrolls.
     - Win32 `SendInput` implementation for keyboard presses, key releases, and Unicode character sequences.
     - Safety guardrails:
       - Parameter bounding: Clamps typing length to max 500 characters per call; limits key sequence lengths.
       - Emergency cancellation: Global keyboard listener for `Ctrl+Alt+Shift+Escape` that immediately drops queued/active input.
       - Corner slam fail-safe: Detects intentional physical mouse slam to `(0, 0)` during automated input sequences (distinguishing deliberate emergency slam from a normal input call targeting `(0, 0)`).
       - Dry-run / test mode: Flag to validate coordinate math and parameters without calling physical `SendInput` during automated tests.
  2. Extend `rust/tom-engine/src/ipc/handlers.rs`:
     - Register low-level handlers under `input.*`: `input.mouse_click`, `input.mouse_move`, `input.mouse_position`, `input.keyboard_press`, `input.keyboard_type`, and `input.status`.
  3. Update Python IPC layer:
     - Register `input.*` method names in `python/tom/ipc/protocol.py`.
     - Create typed wire models in `python/tom/schemas/ipc.py`.
     - Implement typed client methods in `EngineClient` (`python/tom/core/engine.py`): `mouse_click()`, `mouse_move()`, `get_mouse_position()`, `keyboard_press()`, `keyboard_type()`.
- **Files Affected**:
  - `rust/tom-engine/src/input/mod.rs`
  - `rust/tom-engine/src/input/mouse.rs`
  - `rust/tom-engine/src/input/keyboard.rs`
  - `rust/tom-engine/src/input/manager.rs` (new)
  - `rust/tom-engine/src/ipc/handlers.rs`
  - `rust/tom-engine/tests/integration_input.rs` (new)
  - `python/tom/ipc/protocol.py`
  - `python/tom/core/engine.py`
  - `tests/unit/ipc/test_engine_client.py`
- **Existing Components Reused**:
  - `IpcDispatcher` and `IpcResponse` in Rust.
  - `NamedPipeIpcClient` and `EngineClient` in Python.
- **New Interfaces / Types**:
  - Rust: `InputManager`, `InputConfig`, `InputResult`, `VirtualScreenBounds`.
  - Python: `MouseClickRequest`, `MouseMoveRequest`, `KeyboardTypeRequest`, `KeyboardPressRequest`.
- **Tests**:
  - Rust unit tests in `src/input/manager.rs`: Verify coordinate clamping across negative/positive monitor bounds, character chunking, parameter bounds rejection, and cancel flag tripping.
  - Rust integration tests in `tests/integration_input.rs`: Verify IPC request/response cycles for all `input.*` endpoints in dry-run mode (zero physical cursor movement).
  - Python unit tests in `tests/unit/ipc/test_engine_client.py`: Verify client serialization, error translation, and response unpacking.
- **Acceptance Criteria**:
  - `input.mouse_click` and `input.keyboard_type` respond via IPC with engineering target < 10ms latency.
  - Out-of-bounds coordinates are clamped to valid screen geometry without unhandled panics.
  - Triggering the emergency cancel hotkey immediately aborts input processing.
  - Automated tests do not move the physical mouse or type into open applications.
  - Cargo test and cargo clippy pass with 0 warnings.
- **Security Considerations**:
  - Maximum typing length clamped to 500 characters per call.
  - Rust validates focus and parameters independently of Python.
- **Performance Considerations**:
  - Direct Win32 `SendInput` calls introduce < 1ms overhead per event.
- **Dependencies**: Windows API (`windows-sys` or `winapi` with `Win32_UI_Input_KeyboardAndMouse`).

---

### Iteration 1: Screen Capture & Privacy

- **Objective**: Implement high-speed in-memory screen capture with multi-monitor discovery, DPI scaling conversion, and an ephemeral, policy-controlled privacy lifecycle.
- **Implementation Work**:
  1. Create `python/tom/schemas/vision.py`:
     - Schemas: `MonitorInfo`, `ScreenDimensions`, `CapturedFrame`, `WindowInfo`, `PrivacyCheckResult`.
  2. Create `python/tom/vision/privacy.py`:
     - `PrivacyShield`:
       - Foreground window title inspection heuristics against configurable sensitive patterns (`*password*`, `*bitwarden*`, `*1password*`, `*keepass*`, `*bank*`, `*private browsing*`, `*incognito*`).
       - Raises `SensitiveScreenContentError` or flags capture as blocked if a sensitive window is detected.
       - Enforces ephemeral RAM lifecycle: frames live only in memory during the request lifetime and are immediately eligible for garbage collection.
       - Verifies that no image payloads or base64 strings enter logging or telemetry streams.
  3. Create `python/tom/vision/capture.py`:
     - `ScreenCaptureService`:
       - Discovers connected monitors and geometry (primary, virtual desktop bounds, individual monitor coordinates).
       - In-memory capture using `mss` (or Pillow `ImageGrab` fallback) returning raw RGBA/RGB bytes and Pillow `Image` objects in RAM.
       - Zero disk persistence: no files written to disk, temp directories, or caches by default.
       - Supports capturing full virtual desktop, specific monitor index, or specific bounding box/window rect.
       - Converts DPI-scaled screen coordinates to canonical virtual-desktop coordinates.
- **Files Affected**:
  - `python/tom/schemas/vision.py` (new)
  - `python/tom/vision/__init__.py`
  - `python/tom/vision/privacy.py` (new)
  - `python/tom/vision/capture.py` (new)
  - `tests/unit/vision/test_privacy.py` (new)
  - `tests/unit/vision/test_capture.py` (new)
- **Existing Components Reused**:
  - `tom.core.config` for privacy keywords and capture settings.
  - Structured logging via `tom.telemetry.logging` (ensuring images are excluded).
- **New Interfaces / Types**:
  - `ScreenCaptureService` class with `capture_screen()`, `get_monitors()`, `capture_region()`.
  - `PrivacyShield` class with `is_window_sensitive()`, `filter_extracted_text()`, `enforce_ephemeral_lifecycle()`.
- **Tests**:
  - `tests/unit/vision/test_privacy.py`: Verify window title blacklist matching (case-insensitivity, substring, wildcard); verify regex redaction of credit cards, SSNs, and API keys.
  - `tests/unit/vision/test_capture.py`: Mock screen capture backend; verify multi-monitor selection, region cropping, and DPI coordinate scaling.
- **Acceptance Criteria**:
  - Full HD screen capture completes with engineering target < 30ms in memory.
  - Sensitive window titles trigger immediate privacy blocks before frame processing.
  - Zero image files are written to disk during capture operations.
  - Zero base64 image strings appear in telemetry or log outputs.
- **Security Considerations**:
  - Screenshots are held ephemerally in RAM; discarded immediately after processing.
  - Sensitive windows cannot be screenshotted by the agent.
- **Performance Considerations**:
  - In-process capturing avoids multi-megabyte JSON-RPC IPC payloads over Named Pipes.
- **Dependencies**: `mss>=9.0.0` or `Pillow>=10.0.0`.

---

### Iteration 2: OCR & Fast Computer Vision

- **Objective**: Implement ultra-fast CPU-first OCR returning structured results and classical contour-based UI element detection requiring 0 VRAM and targeting < 100ms execution.
- **Implementation Work**:
  1. Create `python/tom/vision/ocr.py`:
     - `OCRProvider` abstract base class:
       - `extract_text(image: Image) -> OCRResult`
       - `find_text(image: Image, query: str) -> list[TextLocation]`
     - Structured `OCRResult` schema:
       ```python
       class OCRResult(BaseModel):
           text: str
           confidence: float
           bounding_boxes: list[BoundingBox]
           source_region: ScreenDimensions | None = None
       ```
     - `MockOCRProvider`: Offline deterministic provider returning synthetic text and bounding boxes for unit tests.
     - `WindowsMediaOCRProvider`: Lazy-loaded provider using native Windows 10/11 `Windows.Media.Ocr` (`winocr`), running on CPU with 0 external binaries and 0 VRAM usage.
     - Production rule: If `winocr` is missing in production, raise a typed `OCRError` or route to local VLM. **Never silently fall back to MockOCRProvider in production.**
     - Optional fallback to `TesseractOCRProvider` if configured.
     - Integration with `PrivacyShield.filter_extracted_text()` to redact API keys, tokens, and credit cards from OCR output (defense-in-depth).
  2. Create `python/tom/vision/cv.py`:
     - `CVElementDetector`:
       - Classical computer vision algorithms (grayscale thresholding, Canny edge detection, contour filtering via OpenCV/Pillow).
       - Detects candidate UI elements: buttons, input fields, cards, modal dialogs, and icons.
       - Returns `list[UIElement]` with normalized bounding boxes `[ymin, xmin, ymax, xmax]`, center points `(x, y)`, and element classifications.
       - Runs entirely on CPU with engineering target < 50ms for 1080p images.
       - Provides clean extension seams for specialized object detectors (e.g. YOLO/SAM) without requiring immediate heavy dependencies.
- **Files Affected**:
  - `python/tom/vision/ocr.py` (new)
  - `python/tom/vision/cv.py` (new)
  - `python/tom/schemas/vision.py`
  - `tests/unit/vision/test_ocr.py` (new)
  - `tests/unit/vision/test_cv.py` (new)
- **Existing Components Reused**:
  - `PrivacyShield` from Iteration 1 for regex token redaction.
  - Abstract provider patterns established in `tom.voice.stt` and `tom.voice.tts`.
- **New Interfaces / Types**:
  - `OCRProvider` ABC, `OCRResult`, `TextLocation`.
  - `CVElementDetector`, `UIElement`, `ElementType` (`BUTTON`, `INPUT_FIELD`, `TEXT_BLOCK`, `ICON`, `CONTAINER`).
- **Tests**:
  - `tests/unit/vision/test_ocr.py`: Test `MockOCRProvider`, structured `OCRResult` serialization, text search, bounding box normalization, and sensitive pattern redaction.
  - `tests/unit/vision/test_cv.py`: Synthetic test images containing rectangles, buttons, and text fields; verify contour detection accurately finds UI element boundaries.
- **Acceptance Criteria**:
  - Text extraction on a test screenshot meets engineering target < 100ms on CPU.
  - OCR returns structured bounding boxes and confidence scores, not just raw text.
  - Classical CV detects standard UI button and text box shapes without calling an LLM.
  - Redaction filter scrubs credit cards and API keys from OCR results.
  - All tests execute offline without requiring GPU or external services.
- **Security Considerations**:
  - OCR output is sanitized before passing to agent context or persistent memory.
- **Performance Considerations**:
  - Downscales oversized captures during CV contour passes to preserve CPU cycles.
- **Dependencies**: `opencv-python>=4.8.0`, `winocr` (optional runtime), `numpy>=1.24.0`.

---

### Iteration 3: VLM Abstraction & VisionManager

- **Objective**: Establish a decoupled Vision-Language Model provider protocol and implement `VisionManager` with capability-driven selection and VRAM resource protection. Phase 7 is strictly local-only.
- **Implementation Work**:
  1. Create `python/tom/vision/vlm.py`:
     - `VLMProvider` abstract base class:
       - `analyze_image(request: VLMRequest) -> VLMResponse`
       - `locate_element(image: Image, description: str) -> list[BoundingBox]`
     - `MockVLMProvider`: Offline deterministic provider with canned responses and mock bounding boxes.
     - `LocalVLMProvider`: Generic local provider using `httpx` with an OpenAI-compatible multimodal adapter (`image_url` with base64 data URI). Connects seamlessly to local runtimes such as LM Studio, Bionic, or Ollama without hard-coding runtime identities.
     - Strong error hierarchy: `VLMError`, `VLMConnectionError`, `VLMTimeoutError`, `VLMResponseError`.
     - *No cloud provider*: `CloudVLMProvider` is strictly excluded from Phase 7 runtime.
     - **Coordinate Re-scaling**: When images are resized for VLM inference (e.g. to 1280px), any bounding box or coordinate returned by the VLM must be scaled back to the original screen dimensions before invoking OS input tools.
  2. Create `python/tom/vision/manager.py`:
     - `VisionManager`: Central coordinator for visual perception tasks.
     - **Capability-driven selection** (cheapest sufficient capability first):
       - If the query is pure text extraction -> route to CPU `OCRProvider`.
       - If the query is locating a known button/label -> route to `OCRProvider` + `CVElementDetector`.
       - If the query requires semantic understanding, visual reasoning, or scene description -> escalate to the configured local VLM tier (`Fast VLM`, `Primary VLM`, or `Deep VLM` candidate from `plan.md`).
     - **Resource Manager Seam Integration**:
       - Checks VRAM availability and model residency with the existing telemetry seams before issuing VLM inference.
       - Respects the 8 GB VRAM budget; handles queuing or graceful degradation if the GPU is under memory pressure.
       - Does not hard-code rules to unload other models (e.g. 1.7B router); delegates scheduling to resource management.
- **Files Affected**:
  - `python/tom/vision/vlm.py` (new)
  - `python/tom/vision/manager.py` (new)
  - `python/tom/schemas/vision.py`
  - `tests/unit/vision/test_vlm.py` (new)
  - `tests/unit/vision/test_manager.py` (new)
- **Existing Components Reused**:
  - `tom.models.base` error handling concepts and `httpx` client patterns.
  - `SystemMonitor` GPU telemetry for VRAM awareness.
- **New Interfaces / Types**:
  - `VLMProvider` ABC, `VLMRequest`, `VLMResponse`, `BoundingBox`, `Point2D`.
  - `VisionManager`, `VisionCapability` (`OCR_ONLY`, `ELEMENT_LOCATION`, `FAST_VLM`, `PRIMARY_VLM`, `DEEP_VLM`).
- **Tests**:
  - `tests/unit/vision/test_vlm.py`: Test `MockVLMProvider`, `LocalVLMProvider` request assembly, base64 image encoding, coordinate un-normalization, and timeout handling.
  - `tests/unit/vision/test_manager.py`: Test capability-driven routing (OCR vs VLM), local-only enforcement, error containment, and mock VRAM threshold tripping.
- **Acceptance Criteria**:
  - `LocalVLMProvider` formats standard multimodal payloads correctly for local runtimes.
  - Queries solvable by OCR/CV bypass VLM calls entirely.
  - Resized VLM coordinates accurately map back to canonical virtual-desktop pixels.
  - Cloud transmission is completely disabled and rejected if requested.
  - All tests execute offline deterministically.
- **Security Considerations**:
  - Visual content never leaves the local machine.
- **Performance Considerations**:
  - Images sent to local VLMs are dynamically resized to max 1280px on the longest edge to minimize memory consumption and inference latency.
- **Dependencies**: `httpx>=0.25.0`, `pydantic>=2.5.0`.

---

### Iteration 4: Vision/Input Tools & Permissions

- **Objective**: Expose perception and OS automation capabilities as deterministic tools under `vision.*` and `os.input.*` through `ToolExecutor` and `PermissionEngine` with explicit, data-driven security metadata.
- **Implementation Work**:
  1. Create `python/tom/tools/vision.py`:
     - `vision.capture` (Tier: `SAFE`): Captures current screen / monitor; returns screen dimensions and active window metadata.
     - `vision.ocr` (Tier: `SAFE`): Extracts on-screen text using structured OCR; returns sanitized text, bounding boxes, and confidence.
     - `vision.find_element` (Tier: `SAFE`): Locates UI element by text or description using Tier 1 CV / OCR; returns center coordinates `(x, y)`.
     - `vision.ask` (Tier: `SAFE`): Asks local VLM a question about the screen or a cropped region; returns text answer.
  2. Create `python/tom/tools/input.py`:
     - `os.input.click` (Tier: `ASK_USER`): Clicks at coordinate `(x, y)` with specified button (`left`, `right`, `double`).
     - `os.input.type_text` (Tier: `ASK_USER`): Types text into the active window (clamped to max 500 characters).
     - `os.input.hotkey` (Tier: `ASK_USER`): Sends key combination (e.g. `["ctrl", "s"]`, `["alt", "tab"]`).
     - `os.input.get_cursor_pos` (Tier: `SAFE`): Returns current mouse cursor position `(x, y)`.
  3. Integrate into TOM Tool System:
     - Register all 8 tools in `python/tom/tools/bootstrap.py` (`setup_default_tools`), expanding total built-in tool count from 20 to 28.
     - **Data-Driven Permission Metadata**: Register explicit security levels for every tool in `ToolRegistry` and `python/tom/security/permissions.py`:
       - `vision.capture` -> `SAFE`
       - `vision.ocr` -> `SAFE`
       - `vision.find_element` -> `SAFE`
       - `vision.ask` -> `SAFE`
       - `os.input.get_cursor_pos` -> `SAFE`
       - `os.input.click` -> `ASK_USER`
       - `os.input.type_text` -> `ASK_USER`
       - `os.input.hotkey` -> `ASK_USER`
       *(Do not use name-prefix string matching like `if name.startswith("vision.")` as the security mechanism)*.
     - Add `vision_manager: object | None = Field(default=None)` to `AgentDependencies` in `python/tom/agents/dependencies.py` (ensuring existing calls and tests without `vision_manager` construct cleanly).
- **Files Affected**:
  - `python/tom/tools/vision.py` (new)
  - `python/tom/tools/input.py` (new)
  - `python/tom/tools/bootstrap.py`
  - `python/tom/security/permissions.py`
  - `python/tom/agents/dependencies.py`
  - `tests/unit/tools/test_vision_tools.py` (new)
  - `tests/unit/tools/test_input_tools.py` (new)
- **Existing Components Reused**:
  - `ToolDefinition`, `ToolExecutor`, `ToolRegistry`.
  - `PermissionEngine`, `ConfirmationHook`, `PermissionLevel`.
  - `AgentDependencies` and `AgentOrchestrator`.
- **New Interfaces / Types**:
  - Parameter models: `CaptureParams`, `OcrParams`, `FindElementParams`, `AskParams`, `ClickParams`, `TypeTextParams`, `HotkeyParams`.
- **Tests**:
  - `tests/unit/tools/test_vision_tools.py`: Test all vision tools through `ToolExecutor`; verify `SAFE` auto-execution and parameter validation.
  - `tests/unit/tools/test_input_tools.py`: Test input tools through `ToolExecutor`; verify `ASK_USER` triggers confirmation requirement; verify unauthorized calls are blocked; verify dry-run execution in automated tests.
- **Acceptance Criteria**:
  - All 8 new tools register successfully in `ToolRegistry` (28 total tools).
  - Explicit metadata controls tool authorization; no prefix-based bypass.
  - Unconfirmed calls to `os.input.click`, `type_text`, or `hotkey` raise `ConfirmationRequiredError`.
  - All tools execute through `ToolExecutor` without pipeline bypass.
  - `AgentDependencies` remains backwards-compatible with Phase 4–6 callers.
- **Security Considerations**:
  - Strict enforcement of `ASK_USER` for OS state modifications.
  - Confirmation hook displays exact coordinates or text to be typed.
- **Performance Considerations**:
  - Tools are lightweight wrappers passing async calls to `VisionManager` or `EngineClient`.
- **Dependencies**: Pydantic validation schemas.

---

### Iteration 5: Integration, Benchmarks & Phase 7 Exit Gate

- **Objective**: Validate the complete vision perception and controlled OS input pipeline end-to-end, execute comprehensive layer-by-layer latency benchmarks, update documentation, and perform formal Phase 7 closeout.
- **Implementation Work**:
  1. Integration Testing (`tests/integration/vision/test_vision_pipeline.py`):
     - End-to-end perception flow: Screen capture -> Privacy check -> Structured OCR text extraction -> Element location -> Tool return.
     - End-to-end actuation flow: Agent decides to click -> `ToolExecutor` checks permissions -> User confirms -> IPC call to Rust engine (`input.mouse_click`) -> Windows `SendInput` simulated in dry-run mode / verified.
     - Privacy boundary test: Ensure sensitive window foregrounding halts perception tools cleanly.
     - Fail-safe test: Verify emergency cancel hotkey listener drops input actions.
     - Error containment: Verify VLM failure or missing OCR libraries do not crash the agent loop.
     - **Test safety**: Automated tests must never move the real physical mouse, click real UI, or type into active apps.
  2. Layer-by-Layer Latency Benchmarks (`tests/integration/vision/test_vision_benchmarks.py`):
     - Measure p50 and p95 timings across individual layers:
       - Capture latency: Engineering target < 30ms (1080p).
       - Privacy classification latency: Engineering target < 5ms.
       - CPU OCR extraction latency: Engineering target < 100ms.
       - Classical CV contour detection latency: Engineering target < 50ms.
       - Tool validation & permission evaluation latency: Engineering target < 2ms.
       - Rust input actuation IPC round-trip: Engineering target < 15ms.
       - Local VLM TTFT and inference latency.
       - Model load, unload, and warm-up timings under 8 GB VRAM budget.
  3. Documentation & State Synchronization:
     - Update `setup.md` with Phase 7 testing instructions and verified test counts.
     - Update `STATE.md`, `PROGRESS.md`, and `HANDOFF.md` to reflect Phase 7 completion.
- **Files Affected**:
  - `tests/integration/vision/test_vision_pipeline.py` (new)
  - `tests/integration/vision/test_vision_benchmarks.py` (new)
  - `setup.md`
  - `STATE.md`
  - `PROGRESS.md`
  - `HANDOFF.md`
- **Existing Components Reused**:
  - Full TOM pipeline (Agent, Orchestrator, ToolExecutor, PermissionEngine, EngineClient, tom-engine).
- **New Interfaces / Types**: None (validation suite).
- **Tests**:
  - Full Python test suite run: `pytest tests/unit/ tests/integration/ -v`.
  - Full Rust test suite run: `cargo test` in `rust/tom-engine`.
  - Quality checks: `ruff check`, `ruff format --check`, `cargo fmt --check`, `cargo clippy`.
- **Acceptance Criteria**:
  - All new unit and integration tests pass with zero regressions across Phases 1–6.
  - Benchmarks satisfy latency thresholds (p50/p95 reported).
  - Zero Ruff or Clippy violations.
  - Documentation accurately reflects the new test baseline and 28 built-in tools.
- **Security Considerations**:
  - Full audit of permission enforcement across all integration scenarios.
- **Performance Considerations**:
  - Benchmarks report millisecond-level execution timings and VRAM consumption.
- **Dependencies**: None.

---

## 9. Tests for Every Iteration

| Iteration | Unit Tests | Integration Tests | Target Test Files |
|:---|:---|:---|:---|
| **Iter 0** | Rust InputManager coordinate clamping, parameter bounds, cancel flag; Python client serialization | Rust IPC `input.*` request/response in dry-run mode | `rust/tom-engine/src/input/manager.rs`, `tests/integration_input.rs`, `tests/unit/ipc/test_engine_client.py` |
| **Iter 1** | Window title blacklist, regex redaction, monitor discovery, DPI scaling, ephemeral RAM check | In-memory frame capture & region cropping (mock backend) | `tests/unit/vision/test_privacy.py`, `tests/unit/vision/test_capture.py` |
| **Iter 2** | Mock OCR, structured OCRResult, text search, contour CV detection, element classification | OCR + CV end-to-end perception on synthetic desktop images | `tests/unit/vision/test_ocr.py`, `tests/unit/vision/test_cv.py` |
| **Iter 3** | Mock VLM, LocalVLMProvider payload builder, VisionManager capability selection, VRAM guardrails | Multi-tier capability fallback on simulated error/timeout; local-only enforcement | `tests/unit/vision/test_vlm.py`, `tests/unit/vision/test_manager.py` |
| **Iter 4** | Vision tools (`SAFE`), OS input tools (`ASK_USER`), parameter bounds, data-driven PermissionEngine checks | Agent loop tool execution with Mock confirmation hook (dry-run input) | `tests/unit/tools/test_vision_tools.py`, `tests/unit/tools/test_input_tools.py` |
| **Iter 5** | Complete suite regression verification | End-to-end perception -> actuation pipeline; layer-by-layer latency benchmarks (p50/p95) | `tests/integration/vision/test_vision_pipeline.py`, `tests/integration/vision/test_vision_benchmarks.py` |

---

## 10. Security Considerations

1. **Explicit Data-Driven Permissions**:
   - Every tool registers its authorization tier explicitly in `ToolRegistry` and `PermissionEngine`.
   - `vision.capture`, `vision.ocr`, `vision.find_element`, `vision.ask` are observation-only -> `SAFE`.
   - `os.input.get_cursor_pos` is read-only -> `SAFE`.
   - `os.input.click`, `os.input.type_text`, `os.input.hotkey` alter OS state -> `ASK_USER`.
   - String-prefix matching (e.g. `name.startswith("vision.")`) is strictly forbidden as a security boundary.
   - Any attempt to execute unconfirmed input actions raises `ConfirmationRequiredError`.
2. **Ephemeral Screenshot Lifecycle**:
   - Screen frames exist strictly in volatile RAM as Python objects or byte arrays during tool execution.
   - Frames are never saved to disk, temp folders, caches, or persistent databases.
   - Discarded immediately after perception processing completes.
3. **Sensitive Content & Privacy Shield**:
   - `PrivacyShield` checks foreground window titles against sensitive patterns (banking, password managers, private browsing). If detected, screen capture is refused with `SensitiveScreenContentError`.
   - Extracted OCR text is scanned with regex filters to redact sensitive credentials, API keys, and credit card numbers before entering agent context.
   - Window title and OCR secret filters are recognized as **heuristics only** (defense-in-depth, not absolute guarantees).
4. **Triple Actuation Fail-Safe**:
   - **Emergency Hotkey**: Pressing `Ctrl+Alt+Shift+Escape` in the Rust engine triggers an immediate abort of all active and queued input events.
   - **Physical Corner Slam**: Detecting a physical mouse slam into `(0, 0)` during automated input sequences halts automation (without treating normal requested coordinates at `(0,0)` as an automatic stop).
   - **Parameter Bounding**: Typing sequences are limited to 500 characters per call; mouse coordinates are clamped to monitor bounds.
5. **No Cloud Transmission**:
   - Screenshots and visual tokens are never sent to external or cloud APIs in Phase 7.

---

## 11. Performance and Resource Considerations

1. **8 GB VRAM & 16 GB RAM Budget**:
   - Fast perception (Screen capture, Windows Media OCR, classical CV contour detection) runs entirely on CPU, consuming 0 MB VRAM and < 150 MB RAM.
   - By handling simple text reading and element location on CPU, TOM avoids waking up heavy VLM models for straightforward UI inspection tasks.
   - When a VLM is queried, images are dynamically resized to max 1280px on the longest edge to minimize memory consumption and inference latency.
2. **Zero IPC Frame Overhead**:
   - Capturing screens in Python avoids serializing and transferring multi-megabyte image payloads over Named Pipe IPC.
   - Rust IPC is reserved for lightweight JSON command control (`input.*`, `audio.*`, `system.*`).
3. **Layer-by-Layer Latency Targets (Engineering Targets, p50/p95)**:
   - Screen capture: < 30ms.
   - Privacy check: < 5ms.
   - CPU OCR extraction: < 100ms.
   - Classical CV contour detection: < 50ms.
   - Tool validation & permissions: < 2ms.
   - Rust input actuation round-trip: < 15ms.

---

## 12. Dependency Policy

1. **Core Zero-Heavyweight Mandate**:
   - No heavy ML frameworks (`torch`, `llama-cpp-python`, `transformers`) are required for the base vision subsystem or test suite.
2. **Modular Optional Dependencies**:
   - `mss>=9.0.0` or `Pillow>=10.0.0` for in-memory screen capture.
   - `opencv-python>=4.8.0` for classical CV contour detection.
   - `winocr` for native zero-binary Windows Media OCR on Windows 10/11.
   - `httpx>=0.25.0` (already installed in base `.venv`) for OpenAI-compatible local VLM endpoints.
3. **No Model Weights in Git**:
   - No large model weights or binary blobs may be committed to the repository.
4. **Mock-First Testing Convention**:
   - All tests must pass with 100% offline determinism using `MockOCRProvider` and `MockVLMProvider`.

---

## 13. Logging and Telemetry Rules

Phase 7 logging and telemetry must strictly adhere to privacy boundaries:
1. **Never Log**:
   - Screenshots or cropped image arrays.
   - Base64 image payloads or data URIs.
   - Raw sensitive OCR text prior to redaction.
   - Passwords, tokens, API keys, or credentials.
   - Full sensitive VLM prompts.
   - Indiscriminate active-window titles.
2. **Allowed Telemetry Metadata**:
   - `vision_request_id`: Unique request UUID.
   - `operation`: e.g. `capture`, `ocr`, `find_element`, `ask`, `click`.
   - `selected_capability`: e.g. `OCR_ONLY`, `ELEMENT_LOCATION`, `LOCAL_VLM`.
   - `provider_type`: e.g. `mock`, `winocr`, `local_vlm`.
   - `model_identity`: e.g. `gemma-e4b`, `ministral-3b`.
   - `latency_ms`: Execution timing breakdown.
   - `confidence`: OCR or element match score.
   - `resource_usage`: Memory/VRAM delta.
   - `status`: `success` or `error_code`.

---

## 14. Integration Points with Existing TOM Components

1. **Rust Engine (`tom-engine`)**:
   - Adds `InputManager` under `rust/tom-engine/src/input/`.
   - Registers new IPC endpoints in `src/ipc/handlers.rs` under low-level `input.*` namespace.
2. **Python Core Engine (`EngineClient`)**:
   - Extends `python/tom/core/engine.py` with typed mouse and keyboard actuation methods communicating over Named Pipe IPC.
3. **Tool System (`ToolExecutor`, `ToolRegistry`)**:
   - Extends built-in tools from 20 to 28 via `python/tom/tools/bootstrap.py`.
4. **Security & Permissions (`PermissionEngine`)**:
   - Integrates explicit data-driven metadata in `python/tom/security/permissions.py`.
5. **Agent Framework (`AgentDependencies`, `AgentOrchestrator`)**:
   - Injects `vision_manager: object | None = Field(default=None)` into `AgentDependencies` ensuring backward compatibility with existing tests and callers.

---

## 15. Exit Gate

Phase 7 is complete and ready to close when:
1. All 6 iterations (Iter 0 through Iter 5) are implemented and verified.
2. The full Python test suite passes with zero failures or skipped tests:
   - Baseline 848 Python tests + all new Phase 7 tests.
3. The full Rust test suite passes with zero failures:
   - Baseline 83 Rust tests + all new input integration tests.
4. Quality checks pass with zero warnings:
   - `ruff check python/ tests/` -> 0 violations.
   - `ruff format --check python/ tests/` -> clean.
   - `cargo fmt --check` -> clean.
   - `cargo clippy --all-targets --all-features -- -D warnings` -> clean.
5. All layer-by-layer latency benchmarks meet their required engineering thresholds (p50/p95 reported).
6. Documentation files (`setup.md`, `STATE.md`, `PROGRESS.md`, `HANDOFF.md`) are synchronized.

---

## 16. Verification Requirements

```powershell
# 1. Activate environment
.\.venv\Scripts\Activate.ps1

# 2. Run new Phase 7 unit tests (targeted during implementation)
pytest tests/unit/vision/ tests/unit/tools/test_vision_tools.py tests/unit/tools/test_input_tools.py -v

# 3. Run new Phase 7 integration and benchmark tests
pytest tests/integration/vision/ -v

# 4. Run full Python test suite (only at exit gate verification)
pytest tests/unit/ tests/integration/ -q

# 5. Run Rust tests & checks
cd rust\tom-engine
cargo test
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cd ..\..

# 6. Run Python quality checks
ruff check python/ tests/
ruff format --check python/ tests/
```

---

## 17. Rollback / Recovery Considerations

1. **Hardware / Driver Input Incompatibility & UIPI**: Win32 `SendInput` cannot interact with elevated administrator windows if `tom-engine` runs un-elevated (UIPI limitation). `InputManager` gracefully returns a structured error code (`UIPIBlocked` or `InputInjectionFailed`) without crashing `tom-engine`.
2. **Missing OCR / CV Dependencies in Production**: If native Windows Media OCR or OpenCV is unavailable in production, `VisionManager` routes directly to the local VLM provider tier or raises a structured `OCRError`. **`MockOCRProvider` is never silently substituted in production.**
3. **Emergency Input Runaway**: If an agent emits unintended key sequences or mouse movements, pressing `Ctrl+Alt+Shift+Escape` or physically slamming the mouse to `(0, 0)` immediately terminates input processing.
4. **Local VLM Runtime Offline**: If the configured local VLM runtime (LM Studio / Bionic / Ollama) is offline, `VisionManager` falls back gracefully to Tier 1 fast perception results or raises a structured `VLMConnectionError`.

---

## 18. Phase 7 Closeout Workflow & Mandatory Plan Deletion

Upon completing Iteration 5 and satisfying the Exit Gate, the following mandatory closeout sequence must be executed:

1. **Verify Acceptance Criteria**: Confirm all 6 iterations meet functional and safety requirements.
2. **Run Final Verification Suites**: Execute full Python test suite (`pytest tests/unit/ tests/integration/`) and full Rust test suite (`cargo test`).
3. **Run Quality Checks**: Execute Ruff check, Ruff format, Cargo fmt, and Cargo clippy; verify 0 warnings/diffs.
4. **Record Benchmark Results**: Document p50/p95 timings for capture, OCR, CV, VLM TTFT, and input IPC.
5. **Update State Files**:
   - `STATE.md`: Update test counts, tool count (28 built-ins), and mark Phase 7 CLOSED & COMPLETE.
   - `PROGRESS.md`: Update completed iterations and roadmap status.
   - `HANDOFF.md`: Update verified test baseline, new subsystem details, and next milestone.
6. **Update Setup Guide**: Update `setup.md` with new test counts and verification commands.
7. **Migrate Unique Historical Information**: Ensure all architecture decisions, interface specifications, and benchmark findings are preserved in permanent documentation (e.g. `HANDOFF.md`, architecture guides).
8. **Mandatory Deletion of Implementation Plan**:
   - Delete `docs/development/PHASE7_IMPLEMENTATIONPLAN.md`.
   - *Rationale: Completed implementation plans must not remain in the repository as competing or stale sources of truth.*
9. **Search for Stale References**: Search the repository for references to `PHASE7_IMPLEMENTATIONPLAN.md` and update them to point to permanent documentation or phase records.
10. **Sanity Check**: Run git status to ensure working directory is clean and consistent.
11. **Produce Phase Closeout Report**: Output a concise closeout summary for the user.
12. **Boundary Enforcement**: Do not start Phase 8 as part of Phase 7 closeout.
