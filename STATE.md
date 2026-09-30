# TOM - Current State Snapshot

## Current Status

- **Phase 0–6**: CLOSED & COMPLETE.
- **Phase 7 (Vision & Multimodal / Extended OS Automation)**: PLANNING COMPLETE — implementation NOT started.
- **Latest Verified Test Baseline**: 848 Python tests passed (756 unit + 92 integration) + 83 Rust tests passed (76 unit + 7 integration) = 931 total verified tests.
- **Quality Gates**: Ruff check clean (0 violations), Ruff format clean (0 diffs), Cargo fmt clean, Cargo clippy clean (0 warnings).
- **Active Implementation Plan**: [`docs/development/PHASE7_IMPLEMENTATIONPLAN.md`](file:///c:/Users/vishnuu/Projects/TOM/docs/development/PHASE7_IMPLEMENTATIONPLAN.md).
- **Current / Immediate Next Action**: Begin Phase 7 — Iteration 0 (Rust Input IPC & OS Input Boundary).

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
│   ├── Tools (20 built-ins through ToolExecutor + PermissionEngine)
│   │   ├── System Tools (7: system.ping, system.status, system.cpu, etc.)
│   │   ├── File Tools (6: file.read, file.write, file.list, etc.)
│   │   ├── Memory Tools (5: memory.remember, memory.recall, memory.forget, etc.)
│   │   └── Voice Tools (2: voice.announce, voice.status)
│   ├── Security (PermissionEngine: SAFE, ASK_USER, BLOCK; ConfirmationHook; Sandbox)
│   ├── Memory (Phase 5: MemoryManager, SQLite authoritative store, optional Qdrant, CPU embeddings)
│   ├── Voice (Phase 6: STT FasterWhisper CPU, TTS Kokoro CPU, SpeechFormatter, VoicePipelineManager, VoiceInteractionManager)
│   └── Vision (Phase 7 Planned: ScreenCaptureService, PrivacyShield, OCRProvider, CVElementDetector, VisionManager, LocalVLMProvider)
└── Rust Engine (tom-engine)
    ├── IPC Server (Named Pipe \\.\pipe\tom-engine)
    ├── System Telemetry (CPU, memory, GPU, disk, battery, processes)
    ├── Audio (AudioManager: capture/playback buffers, ring buffer, device enumeration)
    ├── Input (Phase 7: InputManager: SendInput mouse/keyboard primitives, coordinate clamping, cancel hotkey)
    └── Lifecycle & Event Bus
```

---

## Active Architectural Invariants

1. **Phases 0–6 are CLOSED**: Core architecture, tools, agents, memory, and voice are complete, verified, and locked. Do not rewrite or redesign these subsystems.
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
