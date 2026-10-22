# TOM — Current State

Last updated: 2026-10-21

## Now

| Item | Value |
|---|---|
| **Phases 0–8** | CLOSED & COMPLETE |
| **Phase 9 core** | **9.0–9.4 COMPLETE & VERIFIED**; **9.5/9.6 VOICE PIPELINE REPAIR COMPLETE** (mic pause/resume during TTS, TTS config fix, VAD fallback verified) |
| **Phase 10** | **CLOSED — Iterations 1–7 COMPLETE & VERIFIED** (34 canonical tools registered, schema export, data-driven permissions, configuration defaults, security/verification exit gate) |
| **Phase 11** | **CLOSED — Iterations 11.1–11.6 COMPLETE & VERIFIED** (Secret scrubbing via regex catalog + Shannon entropy heuristic, centralized `PathPolicy` jail & sensitive file blacklist, stdin-safe confirmation hook & anti-flooding rate limits, structured `SecurityAuditLogger`, `ToolExecutor` input/output/error scrubbing with `BaseModel` preservation, and 101 concrete adversarial tests) |
| **Phase 12** | **CLOSED & COMPLETE — Iterations 12.1–12.5 COMPLETE & VERIFIED** (Resource governor safety gate, model lifecycle coordinator, priority arbitration & cooperative preemption fully integrated with real resource admission, 19 adversarial concurrency/stress tests including 100+ repeated cycles with zero drift, and full focused validation suite passed) |
| **Phase 12 closeout** | Completed 2026-10-19. ADRs 059–062 documented. Ephemeral implementation plan removed. |
| **Phase 13 (IPC Stabilization & Tool Grounding)** | **COMPLETE — Targeted stabilization** (IPC idle/reconnect reliability, engine health accuracy, tool grounding/anti-fabrication, startup constructor fix) |
| **Phase 14 (Tray Mode & Desktop Integration)** | **COMPLETE — System tray daemon** (pystray integration, engine connection status, mute toggle, CLI/voice mode support, 33 unit tests) |
| **Model runtime** | llama.cpp at `http://127.0.0.1:65400/v1`; generic OpenAI-compatible HTTP provider is the active abstraction |
| **Backend migration** | Bionic/LM Studio is removed from active architecture; no active backend-specific alias remains |
| **Plan** | Temporary Phase 12 implementation plan removed after closeout migration |
| **Next** | Phase 13 — Agent Evaluation, Benchmarks & Hardening (unstarted; requires explicit approval) |
| **Full-repo baseline** | 2026-10-02 Phase 8 exit: **1301 passed** (1218 Python + 83 Rust), **8 skipped**. Ruff/Cargo fmt/clippy clean. **Not rerun** since then. |
| **9.0–9.4 incremental** | Targeted verification **833 passed** across voice, agents, core, and IPC tests. Voice alone: **317**. Full repo **not** re-run. |
| **Phase 10 verification** | **34 tools registered & verified**; **246 affected-module tests passed** (45 Iteration 7 security tests + web/personal/vision/security/integration tests). Schema exports validated; YAML config aligned; `beautifulsoup4>=4.12.0` added to core deps. DNS rebinding mitigated via single-resolution IP pinning with multi-address fallback (connect-level errors only), Host header rewrite (port omitted for HTTPS/HTTP defaults), `sni_hostname` extension (`httpcore/_async/connection.py:107, 151` [EVIDENCE: verified, httpcore 1.0.9]), and `trust_env=False`. |
| **Phase 11 verification** | **514 passed** in focused exit gate (243 security unit + 101 adversarial + 38 pipeline + 71 personal + 35 web + 26 vision), 1 warning (benign third-party `mss.mss` deprecation in `capture.py:70`). Zero failed, zero skipped. Ruff check/format clean; `git diff --check` clean. Secret scanner benchmark verified: 10 KB p50 = 0.4939 ms (< 1.0 ms SLA), throughput 19.77 MB/s. |
| **Phase 12 verification** | **488 passed, 8 skipped** (optional deps `opencv-python`/`winocr`), zero failed in focused validation suite (`tests/unit/resources/`, `tests/unit/agents/`, `test/unit/vision/`, `tests/integration/tools/test_tool_pipeline.py`). Resources suite alone: **105 passed** (19 stress + 16 governor integration + 15 model manager + 12 priority integration + 14 resource manager + 24 policy + 5 telemetry). 100 repeated contention cycles verified zero accounting drift. Ruff check/format clean; `git diff --check` clean. |
| **Phase 13 (IPC/Tools) verification** | **777+ targeted tests passed** (IPC: 68, Core: 55, Tools: 230, Agents: 264, Integration: 87, Voice: 351, Resources: 107, Security: 344). Ruff check/format clean; `git diff --check` clean. |
| **Phase 14 (Tray Mode) verification** | **33 tray unit tests passed**, 1620+ total targeted tests passed. Tray daemon integrated for CLI/voice modes with engine status monitoring. Ruff check/format clean; `git diff --check` clean. |
| **Voice Pipeline Repair (2026-10-21)** | **391 passed, 5 skipped** across voice unit + integration tests. Fixed: TTS model path config (local file vs HF repo), microphone pause/resume during TTS playback to prevent feedback loop, Smart Turn VAD fallback verified. Ruff check/format clean; `pip check` clean. |

## Architecture (locked Phases 0–8; 9.0–9.4 additive; 10.1–10.2 additive; 11.1–11.5 additive; 12.1–12.5 additive)


Python brain ↔ Rust `tom-engine` over Named Pipe `\\.\pipe\tom-engine`.

| Layer | Owns |
|---|---|
| **Python** | Router; `Agent` / `AgentOrchestrator` (6-state; **9.2** `stream()`); **34 tools** via `ToolExecutor` + `PermissionEngine`; `MemoryManager`; Phase 6 voice (`STTProvider`, `TTSProvider`, `VoicePipelineManager`, `VoiceInteractionManager`); Phase 7 vision; Phase 8 `TaskManager` / `TaskPlanner` / `TaskExecutor` / `ResourceManager` / `ProactiveScheduler`; **9.0** `MicrophoneLoop` + `VADDetector`; **9.1** `StreamingSTTProvider`; **9.2** `SentenceStreamer`; **9.3** `StreamingTTSProvider` + `IncrementalTTSStreamer`; **9.4** `ConversationTurnManager` + `ResponsePolicy`; **10.1** `web.search` (local SearXNG); **10.2** `web.fetch` (SSRF-safe, sanitized extraction); **10.3+4** `personal.note` / `personal.calendar` / `personal.reminder` (SQLite-backed, ProactiveScheduler-integrated); **10.5** `vision.analyze` (VisionManager-delegated, ephemeral/privacy-shielded); **10.6** Tool integration (canonical 34-tool registration, schema export, data-driven permissions, config alignment); **11.1** `SecretScrubber` (regex catalog + Shannon entropy $H \ge 4.5$, fail-safe redaction); **11.2** `PathPolicy` (centralized path jail, ADS/device/relative-drive/symlink blocking, sensitive file blacklist); **11.3** `ConsoleConfirmationHook` (stdin-safe, rate-limited) + `SecurityAuditLogger` (append-only); **11.4** `ToolExecutor` (end-to-end secret scrubbing, BaseModel preservation); **12.1** `ResourceManager` (atomic admission, 500 MB headroom, engine telemetry cache, heavy-lease scoping); **12.2** `ModelLifecycleCoordinator` (state machine UNLOADED/READY/BUSY/IDLE/ERROR, idle eviction hooks); **12.3** `PriorityResourceQueue` (starvation aging, FIFO, `TaskPriority` arbitration); **12.4** Concurrency stress verification (19 adversarial tests, 100+ cycles, zero drift); **12.5** Final validation & closeout |
| **Rust** | IPC server; telemetry; `AudioManager` (capture/playback, ring buffer); `InputManager` (`SendInput`, clamp, cancel hotkey) |

Tools (34): system 7, file 6, memory 5, voice 2 (`voice.announce`, `voice.status` SAFE), vision 5 (`vision.capture`, `vision.ocr`, `vision.find_element`, `vision.ask`, `vision.analyze` SAFE), OS input 4 (`get_cursor_pos` SAFE; `click` / `type_text` / `hotkey` ASK_USER), web 2 (`web.search`, `web.fetch` SAFE), personal 3 (`personal.note`, `personal.calendar` SAFE reads / ASK_USER mutations; `personal.reminder` SAFE reads / ASK_USER mutations with ProactiveScheduler lifecycle).

## Invariants (do not violate)

1. **Extend, do not replace** closed Phase 0–8 subsystems (`AudioManager`, `VoicePipelineManager`, `ToolExecutor`, `PermissionEngine`, `MemoryManager`, `VisionManager`, `InputManager`, `AgentOrchestrator.run()`, `CancellationToken`, `ResourceManager`).
2. Deterministic routing, permissions, clamping, sentence parsing, and audio/task state machines stay **outside** LLM prompts.
3. All agent tools go through `ToolExecutor` → `PermissionEngine`. Permissions are **data-driven** (`DEFAULT_TOOL_PERMISSIONS`), never string-prefix inferred.
4. **SQLite** is memory ground truth; Qdrant is optional index only.
5. Voice turns and screen frames are **ephemeral RAM**; no raw speech/screenshots into SQLite/Qdrant.
6. **Local-only**: no cloud LLM/VLM, no cloud fallback, no remote screenshot send.
7. Offline tests: no GPU, network, or neural-weight downloads.
8. **VAD owns speech boundaries** (Phase 9). STT must not add a competing endpoint detector.
9. Python never injects mouse/keyboard; OS input is Rust `InputManager` via IPC.
10. **Ingress/Egress Secret Sanitization**: All tool results, errors, confirmation displays, and standard log entries undergo secret scrubbing (`SecretScrubber`). If scrubbing encounters an error, fail-safe redaction (`***REDACTION_ERROR***`) applies.
11. **Path Containment & Blacklist**: All filesystem access in `files.py` routes through `PathGuard` → `PathPolicy`. ADS colons, device paths, cross-drive relative paths, and blacklisted sensitive files (`.env`, `id_rsa`, etc.) are unconditionally rejected.
12. **Confirmation Non-Blocking & Audit Invariant**: Interactive confirmations must never block in headless/CI environments (`isatty()` check). If security audit logging fails during confirmation, the operation is denied.
13. **Result Type Preservation**: `ToolExecutor` secret scrubbing must preserve typed `BaseModel` instances in `ToolResult.data` to prevent breaking tool result contracts.
14. **Lease Lifecycle & Inference Safety Invariant**: Incompatible heavy inference roles (reasoning vs. VLM) are mutually exclusive. Reasoning leases are released before tool dispatch to prevent nested deadlocks. Streaming generation leases remain held until client generator `.aclose()` completes to avoid re-granting VRAM while the server process may still be active.
15. **Software Reservation vs. Physical VRAM Invariant**: Resource budgeting is enforced against internal software reservation limits and counters (`_allocated_vram_mb`), with a 500 MB safety headroom margin. Physical VRAM cannot be guaranteed server-side.
16. **Priority Arbitration & Fairness Invariant**: When capacity frees, queued waiters are admitted by effective priority (`TaskPriority`, with starving background tasks promoted after 15s intervals) and FIFO sequence order. Priority ordering never bypasses safety headroom or mutual exclusion. Cooperative preemption tokens signal background tasks to yield; preemption is never forced termination.

## Hardware / env

- Windows 11; Python 3.11+ `.venv`; Rust stable `rust/tom-engine`
- Intel i9-13900H, **16 GB RAM**, RTX 4060 Laptop **8 GB VRAM**
- CPU-first STT/TTS/OCR/CV; VRAM reserved for primary LLM. `ResourceManager`: 500 MB VRAM headroom; `_heavy_lock` between heavy LLM (~5.2 GB) and VLM (~5.5 GB)
- STT FasterWhisper int8 CPU; TTS Kokoro CPU (0 VRAM)

## Phase 9 so far (done)

- **9.0** `MicrophoneLoop`: 16 kHz 16-bit mono ~20 ms frames → energy VAD → `SpeechStartEvent` → `AudioFrameEvent*` → `SpeechEndEvent`.
- **9.1** `StreamingSTTProvider` / `FasterWhisperStreamingAdapter`: VAD-delimited PCM → partials + final; no second endpointer.
- **9.2** `AgentOrchestrator.stream()` (token/delta generator; `run()` unchanged) + `SentenceStreamer` (speakable chunks).
- **9.3** `StreamingTTSProvider` (sentence → ordered `SynthesisChunk`) + `IncrementalTTSStreamer`: sentence N plays while N+1 synthesizes; cancellation discards pending work and calls `stop_audio_playback()` exactly once.
- **9.4** `ConversationTurnManager` (turn lifecycle, bounded history, entity referents, Phase 8 `TaskManager` snapshots) + `ResponsePolicy` (`SPEAK_DIRECT` / `FAST_ACK_THEN_WORK` / `SILENT_ACT` / `CLARIFY` / `REPORT_PROGRESS`). Referent *resolution* stays with the model; no NLP engine.
- **9.B WS0** Measurement integrity: complete and documented as preliminary from feed start, not speech onset.
- **9.B WS1** Cheap faster-whisper tuning: complete; beam_size=1 produced the best tested profile, but latency remains above the 350 ms target and requires separate acceptance work.
- **9.B WS2** Streaming-native STT evaluation: audit/superseded by the Checkpoint 1 remeasurement; no Phase 10 implementation is implied.
- **9.B WS3** Turn detection & Smart Turn: **STABILIZED & VERIFIED UNDER REVISION** (Smart Turn V3 optionalized in pyproject.toml `smart-turn` extra; safe lazy initialization with `SmartTurnUnavailableError` and `check_is_available()`; deterministic fallback to VAD speech-end without double-commit or audio loss; CPUExecutionProvider confirmed with bundled `smart-turn-v3.2-cpu.onnx`; 100% false-complete on hesitation prefixes confirmed via benchmark [0/15 correct], proving Smart Turn cannot serve as sole endpointer and must remain paired with VAD hangover; 64 voice tests passing including 6 new fallback/safety tests).

Files, gaps, and 9.5 contract: **`HANDOFF.md`**. Ledger and ADRs: **`PROGRESS.md`**. Phase 9.5/9.6 are intentionally deferred.
