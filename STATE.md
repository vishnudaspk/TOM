# TOM — Current State

Last updated: 2026-10-04

## Now

| Item | Value |
|---|---|
| **Phases 0–8** | CLOSED & COMPLETE |
| **Phase 9** | IN PROGRESS — Iteration **9.0 COMPLETE & VERIFIED**; **9.1 not started** |
| **Plan** | `docs/development/PHASE9_IMPLEMENTATIONPLAN.md` |
| **Next** | Iteration **9.1 Streaming STT**, only when instructed. Do not implement 9.1+. |
| **Full-repo baseline** | 2026-10-02 Phase 8 exit: **1301 passed** (1218 Python + 83 Rust), **8 skipped**. Ruff/Cargo fmt/clippy clean. |
| **9.0 incremental** | 17 new unit tests (`tests/unit/voice/test_microphone_loop.py`). Targeted voice suite green (**220** = 197 unit incl. 17 new + 23 integration). **Full repo suite not re-run.** |

## Architecture (locked Phases 0–8; 9.0 additive)

Python brain ↔ Rust `tom-engine` over Named Pipe `\\.\pipe\tom-engine`.

| Layer | Owns |
|---|---|
| **Python** | Router; `Agent` / `AgentOrchestrator` (6-state); 28 tools via `ToolExecutor` + `PermissionEngine`; `MemoryManager`; Phase 6 voice (`STTProvider`, `TTSProvider`, `VoicePipelineManager`, `VoiceInteractionManager`); Phase 7 vision; Phase 8 `TaskManager` / `TaskPlanner` / `TaskExecutor` / `ResourceManager` / `ProactiveScheduler`; **9.0** `MicrophoneLoop` + `VADDetector` |
| **Rust** | IPC server; telemetry; `AudioManager` (capture/playback, ring buffer); `InputManager` (`SendInput`, clamp, cancel hotkey) |

Tools (28): system 7, file 6, memory 5, voice 2 (`voice.announce`, `voice.status` SAFE), vision 4 (`SAFE`), OS input 4 (`get_cursor_pos` SAFE; `click` / `type_text` / `hotkey` ASK_USER).

## Invariants (do not violate)

1. **Extend, do not replace** closed Phase 0–8 subsystems (`AudioManager`, `VoicePipelineManager`, `ToolExecutor`, `PermissionEngine`, `MemoryManager`, `VisionManager`, `InputManager`, `AgentOrchestrator.run()`, `CancellationToken`, `ResourceManager`).
2. Deterministic routing, permissions, clamping, and audio/task state machines stay **outside** LLM prompts.
3. All agent tools go through `ToolExecutor` → `PermissionEngine`. Permissions are **data-driven** (`DEFAULT_TOOL_PERMISSIONS`), never string-prefix inferred.
4. **SQLite** is memory ground truth; Qdrant is optional index only.
5. Voice turns and screen frames are **ephemeral RAM**; no raw speech/screenshots into SQLite/Qdrant.
6. **Local-only**: no cloud LLM/VLM, no cloud fallback, no remote screenshot send.
7. Offline tests: no GPU, network, or neural-weight downloads.
8. **VAD owns speech boundaries** (Phase 9). STT must not add a competing endpoint detector.
9. Python never injects mouse/keyboard; OS input is Rust `InputManager` via IPC.

## Hardware / env

- Windows 11; Python 3.11+ `.venv`; Rust stable `rust/tom-engine`
- Intel i9-13900H, **16 GB RAM**, RTX 4060 Laptop **8 GB VRAM**
- CPU-first STT/TTS/OCR/CV; VRAM reserved for primary LLM. `ResourceManager`: 500 MB VRAM headroom; `_heavy_lock` between heavy LLM (~5.2 GB) and VLM (~5.5 GB)
- STT FasterWhisper int8 CPU; TTS Kokoro CPU (0 VRAM)

## Phase 9.0 (done)

`MicrophoneLoop`: 16 kHz 16-bit mono ~20 ms frames → energy VAD → `SpeechStartEvent` → `AudioFrameEvent*` → `SpeechEndEvent`. Mute/pause/stop. See `HANDOFF.md` for files, gaps, 9.1 contract.
