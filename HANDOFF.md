# TOM Developer Handoff — Ready for Phase 7

## Current Status

- **Phases 0–6**: CLOSED & COMPLETE.
- **Current Milestone**: Phase 7 — Vision & Multimodal Capabilities / Extended OS Automation.
- **Active Implementation Plan**: [`docs/development/PHASE7_IMPLEMENTATIONPLAN.md`](file:///c:/Users/vishnuu/Projects/TOM/docs/development/PHASE7_IMPLEMENTATIONPLAN.md).
- **Verified Test Baseline**: 848 Python tests (756 unit + 92 integration) + 83 Rust tests (76 unit + 7 integration) = **931 total verified tests**.
- **Quality Gates**: Ruff check clean (0 violations), Ruff format clean (0 diffs), Cargo fmt clean, Cargo clippy clean (0 warnings).
- **Baseline Date**: 2026-09-30.

---

## Resume Point: Phase 7 — Iteration 0

The next coding session should begin immediately with **Phase 7 — Iteration 0: Rust Input IPC & OS Input Boundary**.

### Immediate Implementation Tasks (Iteration 0)

1. **Rust Engine Input Manager (`rust/tom-engine/src/input/manager.rs`)**:
   - Implement `InputManager` orchestrating canonical virtual-desktop coordinate clamping, parameter bounding, rate limiting, and emergency cancellation.
   - Implement Win32 `SendInput` primitives for mouse moves, clicks (left, right, middle, double), and wheel scrolls.
   - Implement Win32 `SendInput` primitives for keyboard key down/up and Unicode character sequence typing.
   - Add safety guardrails:
     - Parameter limits: Clamped typing length (max 500 chars/call) and bounded key sequences.
     - Emergency hotkey: Global listener for `Ctrl+Alt+Shift+Escape` that immediately aborts active/queued inputs.
     - Corner slam fail-safe: Detects physical mouse slam into `(0, 0)` during automation (without confusing valid requested coordinates).
     - Dry-run / test mode: Flag to validate coordinate math and parameters without calling physical `SendInput` during automated tests.
2. **Rust IPC Handlers (`rust/tom-engine/src/ipc/handlers.rs`)**:
   - Register low-level IPC handlers under `input.*`:
     - `input.mouse_click`
     - `input.mouse_move`
     - `input.mouse_position`
     - `input.keyboard_press`
     - `input.keyboard_type`
     - `input.status`
3. **Python IPC Layer**:
   - Register `input.*` IPC method names in `python/tom/ipc/protocol.py`.
   - Add typed wire models in `python/tom/schemas/ipc.py`.
   - Extend `EngineClient` in `python/tom/core/engine.py` with typed methods (`mouse_click`, `mouse_move`, `get_mouse_position`, `keyboard_press`, `keyboard_type`).
4. **Targeted Tests**:
   - Rust unit tests in `src/input/manager.rs`.
   - Rust integration tests in `tests/integration_input.rs` (using dry-run mode).
   - Python unit tests in `tests/unit/ipc/test_engine_client.py`.

---

## Critical Rules & Boundaries for Phase 7

1. **Local-Only Runtime**:
   - Phase 7 runtime is strictly local. No cloud LLM/VLM execution, no cloud fallback, and no remote screenshot transmission.
   - `CloudVLMProvider` is future-only and must not be implemented or activated in Phase 7.
2. **Python vs. Rust Boundary**:
   - Python owns screen capture, image preprocessing, OCR, OpenCV, optional YOLO/SAM, VLM orchestration, and tool validation.
   - Rust owns mouse/keyboard primitives, canonical coordinate clamping, emergency hotkey, and Windows `SendInput`.
   - Python must **never** directly inject OS input using `pyautogui`, `pywin32`, or `pynput`.
   - High-level tool namespace is `os.input.*`; low-level Rust IPC namespace is `input.*`.
3. **Data-Driven Tool Permissions**:
   - Tool permissions must be explicit metadata in `ToolRegistry` and `PermissionEngine` (`vision.capture`, `vision.ocr`, `vision.find_element`, `vision.ask` $\rightarrow$ `SAFE`; `os.input.get_cursor_pos` $\rightarrow$ `SAFE`; `os.input.click`, `os.input.type_text`, `os.input.hotkey` $\rightarrow$ `ASK_USER`).
   - Never use string-prefix matching (e.g. `if name.startswith("vision.")`) as a security mechanism.
4. **Ephemeral Privacy Lifecycle**:
   - Screen captures exist only in volatile RAM during request execution and are discarded immediately.
   - Never persist screenshots to disk, temporary files, or databases by default.
   - Never log raw image payloads, base64 strings, or unredacted OCR credentials.
   - Never automatically persist vision results into SQLite or Qdrant memory.
5. **Safe Automated Testing**:
   - Automated unit/integration tests must use dry-run mode or mocks; they must **never** move the physical mouse cursor, click real UI, or type into active applications on the user's desktop.
6. **Backward Compatibility**:
   - When updating `AgentDependencies` in `python/tom/agents/dependencies.py`, `vision_manager: object | None = Field(default=None)` must be optional with default `None` to prevent breaking existing tests and call sites.
7. **What NOT to Change**:
   - Do not modify or redesign completed Phase 0–6 code (Rust `AudioManager`, `VoicePipelineManager`, `ToolExecutor`, `MemoryManager`, etc.).
   - Do not bypass the `ToolExecutor` and `PermissionEngine` pipeline.

---

## Testing & Verification Commands

```powershell
# 1. Activate environment
.\.venv\Scripts\Activate.ps1

# 2. Run focused tests during Iteration 0 (do NOT run the full suite for baseline)
pytest tests/unit/ipc/test_engine_client.py -v
cd rust\tom-engine
cargo test input
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cd ..\..

# 3. Full test suite verification (ONLY required at the Phase 7 Exit Gate)
pytest tests/unit/ tests/integration/ -q
cd rust\tom-engine
cargo test
cd ..\..
```
