# TOM - Current State Snapshot

## Current Status

- **Phases 0–7**: CLOSED & COMPLETE.
- **Phase 7 (Vision & Multimodal / Extended OS Automation)**: CLOSED & COMPLETE — Iterations 0–5 complete and verified.
- **Latest Verified Test Baseline**: 1004 Python tests passed + 8 skipped (870 unit + 134 integration) + 83 Rust tests passed = **1087 total passing** (8 skipped are optional-dep cv2/winocr tests).
- **Quality Gates**: Ruff check clean (0 violations), Ruff format clean (0 diffs), Cargo fmt clean, Cargo clippy clean (0 warnings).
- **Active Implementation Plan**: None (Phase 7 closed; awaiting Phase 8 planning).
- **Current / Immediate Next Action**: Phase 8 (Autonomous Proactive Agent & Long-Horizon Task Execution).

---

## High-Level Architecture

TOM is a local-first personal AI operating layer operating across two core engines over Windows Named Pipe IPC (`\\.\pipe\tom-engine`):

```text
TOM
├── Python Brain
│   ├── Router (Qwen3 1.7B intent/model router)
│   ├── Agents (Agent / AgentOrchestrator, 6-state lifecycle)
│   │   └── AgentDependencies (executor, memory_manager, voice_manager, vision_manager [Phase 7])
│   ├── Models (LLMProvider ABC: Mock, LMStudio / Bionic / local OpenAI-compatible)
│   ├── Tools (28 built-ins through ToolExecutor + PermissionEngine; 8 new tools in Phase 7)
│   │   ├── System Tools (7: system.ping, system.status, system.cpu, etc.)
│   │   ├── File Tools (6: file.read, file.write, file.list, etc.)
│   │   ├── Memory Tools (5: memory.remember, memory.recall, memory.forget, etc.)
│   │   ├── Voice Tools (2: voice.announce, voice.status)
│   │   ├── Vision Tools (4: vision.capture, vision.ocr, vision.find_element, vision.ask [SAFE])
│   │   └── OS Input Tools (4: os.input.click [ASK_USER], os.input.type_text [ASK_USER], os.input.hotkey [ASK_USER], os.input.get_cursor_pos [SAFE])
│   ├── Security (PermissionEngine: SAFE, ASK_USER, BLOCK; ConfirmationHook; Sandbox; explicit DEFAULT_TOOL_PERMISSIONS)
│   ├── Memory (Phase 5: MemoryManager, SQLite authoritative store, optional Qdrant, CPU embeddings)
│   ├── Voice (Phase 6: STT FasterWhisper CPU, TTS Kokoro CPU, SpeechFormatter, VoicePipelineManager, VoiceInteractionManager)
│   └── Vision (Phase 7: ScreenCaptureService, PrivacyShield, OCRProvider, CVElementDetector, VisionManager, LocalVLMProvider, Vision Tools, OS Input Tools)
└── Rust Engine (tom-engine)
    ├── IPC Server (Named Pipe \\.\pipe\tom-engine)
    ├── System Telemetry (CPU, memory, GPU, disk, battery, processes)
    ├── Audio (AudioManager: capture/playback buffers, ring buffer, device enumeration)
    ├── Input (Phase 7: InputManager: SendInput mouse/keyboard primitives, coordinate clamping, cancel hotkey)
    └── Lifecycle & Event Bus
```

---

## Active Architectural Invariants

1. **Phases 0–7 are CLOSED**: Core architecture, tools, agents, memory, voice, vision, and OS input actuation are complete, verified, and locked. Do not rewrite or redesign these subsystems.
2. **Deterministic Code Outside the LLM**: Tool routing, permission verification, security checks, coordinate clamping, and audio state machines execute in deterministic code, never inside LLM prompts.
3. **Non-Bypassable Tool Execution**: All agent tool calls flow strictly through `ToolExecutor` and `PermissionEngine`.
4. **Data-Driven Tool Permissions**: Tool security levels (`SAFE`, `ASK_USER`, `BLOCK`) are explicitly registered metadata in `ToolRegistry` and `PermissionEngine`, never inferred via string prefixes.
5. **SQLite is Authoritative for Memory**: Qdrant provides optional semantic indexing and never acts as ground truth.
6. **Ephemeral Modality Lifecycles**:
   - Voice conversation turns live in volatile RAM (`ConversationHistory`); raw speech is not dumped into persistent vector memory.
   - Screen captures live ephemerally in RAM during request execution; zero persistent screenshot caching or automatic ingestion into SQLite/Qdrant.
7. **Local-Only Runtime**: No cloud LLM/VLM execution, no cloud fallback, and no remote screenshot transmission in active development phases.
8. **Hardware & Resource Limits**: Intel i9-13900H, 16 GB RAM, RTX 4060 Laptop GPU (8 GB VRAM). CPU-first defaults for STT, TTS, and Tier 1 OCR/CV preserve scarce VRAM for primary LLM inference.
9. **Offline Deterministic Tests**: Automated unit/integration tests must never require GPU availability, external network access, or downloaded neural weights.

---

## Environment & Hardware Baseline

- **OS**: Windows 11
- **Python**: 3.11+ in `.venv` (dependencies: Pydantic v2, HTTPX, PyYAML, psutil, pytest, ruff)
- **Rust**: `rustc` / `cargo` stable (`rust/tom-engine`)
- **IPC Pipe**: `\\.\pipe\tom-engine`
- **GPU / VRAM**: NVIDIA GeForce RTX 4060 Laptop GPU (8 GB VRAM)
- **System Memory**: 16 GB RAM
