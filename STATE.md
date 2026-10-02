# TOM - Current State Snapshot

## Current Status

- **Phases 0–8**: CLOSED & COMPLETE.
- **Phase 8 (Autonomous Proactive Agent & Long-Horizon Task Execution)**: CLOSED & COMPLETE (5/5 Iterations Verified).
- **Phase 8 Iteration 0**: COMPLETE & VERIFIED — Task schemas, TaskManager, TaskConfig, SQLite audit tables (41 unit tests).
- **Phase 8 Iteration 1**: COMPLETE & VERIFIED — Plan/PlanStep schemas, TaskPlanner DAG validation & cycle detection, LoopDetector (34 unit tests).
- **Phase 8 Iteration 2**: COMPLETE & VERIFIED — TaskExecutor DAG execution, step budgets/timeouts, cancellation, confirmation transitions, checkpointing, VisualRevalidator (18 unit tests).
- **Phase 8 Iteration 3**: COMPLETE & VERIFIED — ModelBenchmarkSuite (TTFT, tokens/sec, schema fidelity, VRAM residency), ResourceManager hardware governor (8 GB VRAM / 16 GB RAM, 500 MB headroom margin, NVML/psutil abstraction, reasoning/VLM mutual exclusion lock), benchmark scaffolding (`bench_llm.py`), RTX 4060 benchmark docs (21 unit/benchmark tests passed, 1 skipped live).
- **Phase 8 Iteration 4**: COMPLETE & VERIFIED — ProactiveScheduler with cron/interval/system-event triggers, QuietHoursConfig (22:00–08:00 overnight window), RateLimitConfig (1 task/30 min), user preemption with CancellationToken, urgent_health bypass, deterministic policy evaluation. Proactive schemas & 65 unit tests (100% offline, 0 GPU required).
- **Phase 8 Iteration 5**: COMPLETE & VERIFIED — End-to-end integration tests (`test_long_horizon_pipeline.py`, `test_confirmation_pipeline.py`, `test_cancellation_pipeline.py` — 36 tests), live empirical benchmark against `qwen3-8b` on RTX 4060 GPU (TTFT: 411.31 ms, TPS: 25.93, 100% schema fidelity, 6,714.4 MB peak VRAM), full regression suite pass.
- **Verified Final Baseline (Phase 8 Exit Gate)**: 1218 Python passed (1048 unit + 170 integration), 8 skipped; 83 Rust passed (76 unit + 7 integration); 1301 total tests passing (0 failures).
- **Quality Gates**: Ruff check clean (0 violations), Ruff format clean (0 diffs), Cargo fmt clean, Cargo clippy clean (0 warnings).
- **Active Implementation Plan**: None (Phase 8 CLOSED; Phase 9 planning pending).
- **Current / Immediate Next Action**: Phase 8 Exit Gate fully satisfied. All 5 iterations verified and documented. Await user instruction for Phase 9 roadmap.


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
