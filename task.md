# TOM — Master Task Backlog & Architecture Roadmap

**File**: `task.md`  
**Location**: Repository Root (`c:\Users\vishnuu\Projects\TOM\task.md`)  
**Status**: Active Master Task Backlog  
**Branch**: `wip/audit-2026-11-02`  
**Last Updated**: 2026-11-05  

---

## 1. Document Purpose & Operational Rules

1. **Single Source of Truth**: This document is the master engineering backlog for transforming TOM into a reliable, modular, local-first, voice-enabled personal AI assistant with JARVIS-like capabilities.
2. **Grounded in Verified Reality**: Every task is substantiated by actual file:line citations, verified runtime logs, or passing/failing test suites—never speculative architecture claims.
3. **Invariants Preserved**:
   - Closed Phase 0–8 subsystems and Phase 11–12 security/resource invariants must be extended, never broken.
   - Deterministic logic, state machines, permissions, and routing stay outside LLM prompts.
   - Local-only execution: no cloud LLM/VLM dependencies for core workflows.
   - Ephemeral raw audio and screen frames in RAM; SQLite is memory ground truth.
4. **Maintenance**: When a task is completed, update its status to `DONE_VERIFIED`, cite the verification commit or test command, and run `graphify update .` if code files were modified.

---

## 2. Repository Snapshot (Ground Truth)

- **Branch**: `wip/audit-2026-11-02` (11 unstaged modified files representing active wake-word UX polish, preserved as-is)
- **Unit Test Files**: 96 test files across 14 modules in `tests/unit/`
- **Integration Test Files**: 20 test files in `tests/integration/`
- **End-to-End Test Files**: 2 files in `tests/e2e/` (`test_cli_real_path.py`, `conftest.py`)
- **Canonical Tools**: 34 registered tools across 8 domains (system 7, files 6, memory 5, voice 2, vision 5, input 4, web 2, personal 3)
- **LLM Runtime**: External `llama.cpp` at `http://127.0.0.1:65400/v1` via generic OpenAI-compatible `HttpModelProvider`
- **STT Runtime**: `FasterWhisper` (`small.en`) running on CUDA (`int8_float16`) with memoized CPU fallback
- **TTS Runtime**: `Kokoro-82M` (`kokoro-v0_19.onnx`) running on CPU via ONNX Runtime
- **Assistant Identity**: `TOM` (`python/tom/identity.py`), time-aware greetings in `python/tom/voice/greetings.py`
- **Measured Latency**: Perceived turnaround p50 = 6,469ms (target: 1,500ms); STT finalization p50 = 3,484ms (target: 350ms)

---

## 3. Subsystem Completion Assessment

| Subsystem / Phase | Declared Status | Audited Reality & Verification Notes | Confidence |
|---|---|---|---|
| **Phases 0–8** (Foundations, Engine, IPC, Tools, Agents, Memory, Voice v1, Vision v1, Proactive) | CLOSED | 1,301 tests passing at Phase 8 exit (1,218 Python + 83 Rust). Invariants established. | HIGH |
| **Phase 9** (Streaming Voice 9.0–9.4) | CLOSED | VAD, streaming STT, sentence streamer, incremental TTS, turn manager verified (833 targeted tests). | HIGH |
| **Phase 10** (34 Tools & Schemas) | CLOSED | 34 canonical tools registered; JSON schemas exported; permissions aligned (777+ targeted tests). | HIGH |
| **Phase 11** (Security & Isolation) | CLOSED | `SecretScrubber` (regex + Shannon entropy), `PathPolicy` jail, audit logger (514 focused tests, 101 adversarial). | HIGH |
| **Phase 12** (Resource Management) | CLOSED | 500MB VRAM headroom, `ModelLifecycleCoordinator`, `PriorityResourceQueue` (488 focused tests, 100 cycles zero-drift). | HIGH |
| **Phase 13** (IPC & Tool Grounding) | CLOSED | Reconnection cycles, anti-fabrication prompt constraints, startup constructors verified. | HIGH |
| **Phase 14** (Tray Mode & Desktop) | CLOSED | System tray daemon (`pystray`), engine connection monitoring, mute toggle (33 unit tests). | HIGH |
| **Phase V1** (Voice Pipeline Repairs) | COMPLETE | Items 0–6 verified (1,771 unit tests passed); GPU STT, markdown stripping, metric honesty. | HIGH |
| **Phase V2** (Response Latency) | PARTIAL | Step 0 (Reconcile) complete; Steps 1–5 (Live Baseline, TTS opt, LLM path, endpointing, barge-in) pending. | MEDIUM |
| **Wake-Word UX Phase** | VERIFIED | Homophone regex, punctuation tolerance, silent boot, sleep after 30s timeout verified (59 tests passed). | MEDIUM |
| **Task Management** (`TaskManager`) | IMPLEMENTED | 9-state lifecycle, DAG planner, cancellation tokens, loop detector. Registry volatile in RAM. | HIGH |
| **Memory Architecture** | IMPLEMENTED | SQLite authoritative store, optional Qdrant semantic index. Dual-delete and consistency unverified. | MEDIUM |
| **Vision System** | IMPLEMENTED | OCR and CV ephemeral analysis functional. Local VLM (Qwen2.5-VL-7B) integration untested on physical weights. | MEDIUM |
| **Voice Confirmation** (Security) | GAP | Non-interactive console (`isatty() == False`) causes all `ASK_USER` mutating tools to auto-deny in voice mode. | HIGH |

---

## 4. Critical Blockers & Milestone Acceptance Gates (P0)

### VOICE-001: Barge-In Interruption Detection & Playback Cancellation Path
- **Priority**: P0
- **Status**: PARTIAL
- **Evidence**: `python/tom/voice/microphone.py:L382` (`push_samples` discards all frames when `_gated=True`); `python/tom/voice/interaction_core.py:L1173`; `docs/audits/VOICE_REPAIR_P0_P1_P2_P3_REPORT.md` Section V2 Step 5 (DESIGN ONLY).
- **Exact Problem**: When assistant speech output begins, the microphone capture path is paused/gated to prevent acoustic self-feedback. Because incoming frames are discarded, user speech cannot trigger barge-in during playback. Furthermore, the architecture lacked a clear distinction between the speech-qualification duration and cancellation execution latency.
- **Required Work**:
  1. *Dual-Path Audio Processing*: Implement a dedicated, low-cost acoustic monitoring tap during playback that processes microphone energy without buffering speech for STT until an interruption is confirmed.
  2. *Qualification vs. Cancellation Separation*: Disambiguate timing metrics:
     - *Qualification Window*: Require 200–300ms of sustained user vocal energy above the calibrated acoustic threshold to reject brief transients, clicks, or room noises.
     - *Cancellation Latency*: Once barge-in is confirmed, execute `EngineClient.stop_audio_playback()` and epoch invalidation within <150ms.
  3. *Acoustic Feedback Protection*: Calibrate barge-in energy thresholds relative to measured output speaker volume and room reverberation to prevent assistant speech bleed from falsely triggering self-interruption.
  4. *Physical Hardware Verification*: Validate on physical microphone and speakers with varying output volume levels.
- **Acceptance Criteria**: Speaking during assistant playback cancels audio output in <150ms from confirmation, invalidates the turn epoch, un-gates the primary mic buffer, and transitions pipeline to `LISTENING`. Normal playback must not self-interrupt.
- **Verification Method**: Hermetic test in `tests/unit/voice/test_mic_recovery.py` simulating concurrent playback and interruption audio, followed by live hardware test.
- **Dependencies**: None.
- **Risks**: Acoustic coupling between speakers and microphone requires dynamic threshold margin or software echo suppression.

---

### BENCH-001: Phase V2 Step 1 Live Baseline Latency Measurement
- **Priority**: P0 (Milestone Acceptance Gate)
- **Status**: BLOCKED
- **Evidence**: `STATE.md` ("Next Immediate Task: Phase V2 Step 1 Live Baseline measurement with user_felt_latency_ms"); `docs/perf/00-baseline.md`.
- **Exact Problem**: Current latency optimizations cannot be grounded or verified without an up-to-date baseline recording `user_felt_latency_ms` and segment timings across real acoustic turns.
- **Required Work**:
  1. Instrument and execute a 15-turn live conversational session with active timing telemetry.
  2. Record p50, p90, and p95 percentiles for speech endpoint hangover, STT queue wait, STT decode, LLM TTFT, sentence chunking, TTS first-chunk synthesis, and time-to-first-audio.
  3. Commit baseline measurements to `docs/perf/v2_live_baseline.json`.
- **Acceptance Criteria**: 15 complete turns recorded with zero failures; complete timing breakdown captured in JSON report.
- **Verification Method**: Run live session with `--voice --verbose` and process telemetry with `scripts/extract_latency.py`.
- **Dependencies**: Running `tom-engine.exe`, active `llama.cpp` instance, physical microphone/speakers.
- **Risks**: Variations in user speaking cadence and room noise floor can influence endpointing metrics.

---

### VOICE-002: Live 15-Turn Acoustic Voice Acceptance Gate (P0.8)
- **Priority**: P0 (Acceptance Gate)
- **Status**: BLOCKED
- **Evidence**: `docs/audits/VOICE_REPAIR_P0_P1_P2_P3_REPORT.md` Item P0.8 marked `BLOCKED`.
- **Exact Problem**: Voice pipeline fixes (P0.1–P0.7), watchdog recovery, and wake-word gating have been proven in hermetic unit tests, but real acoustic end-to-end operation has not been signed off.
- **Required Work**: Human operator conducts a 15-turn acoustic conversation covering: initial wake-word activation, time-aware greeting, command execution, 30s dormancy timeout, post-timeout wake-up, follow-up turns, and barge-in interruption.
- **Acceptance Criteria**: 15 consecutive turns succeed with 0 unhandled exceptions, 0 permanent deaf states, and 100% reliable wake-up after inactivity timeout.
- **Verification Method**: Inspection of `logs/tom.log` for event sequences (`speech_onset_detected`, `wakeword_gate_accepted_*`, `voice.turn.decision`, `playback_completed`).
- **Dependencies**: Physical audio hardware, VOICE-001, VOICE-005.
- **Risks**: Environmental background noise or weak microphone gain may require VAD threshold adjustment.

---

## 5. Master Backlog (By Subsystem Workstream)

---

### Workstream 1: VOICE (Voice Activation, Speech Pipeline & Latency)

#### VOICE-003: STT Pipeline Stage Latency Profiling & Model Evaluation
- **Priority**: P1
- **Status**: PARTIAL
- **Evidence**: `docs/perf/00-baseline.md` (`stt_finalization_ms` p50=3,484ms, `stt_decode_ms` p50=1,734ms); `HANDOFF.md` §9.B WS1.
- **Exact Problem**: STT finalization dominates interactive voice turnaround (~3.5s of ~6.5s). While FasterWhisper's 30-second window padding has been hypothesized as a cause, the exact breakdown across audio preparation, queue delay, thread synchronization, inference, and decoder finalization has not been isolated under live profiling.
- **Required Work**:
  1. Add microsecond-precision profiling spans across: audio slicing, tensor conversion, CTranslate2 model inference, token decoding, and punctuation formatting.
  2. Perform head-to-head benchmarking between `FasterWhisper` (`small.en`) and `Moonshine-base` (int8) on the identical corpus (`tests/benchmarks/voice/ws2/`).
  3. Document recognition quality (WER on commands, questions, and proper nouns) alongside latency percentiles.
  4. Select and deploy the optimal configuration only if recognition quality meets assistant requirements.
- **Acceptance Criteria**: Documented breakdown identifying the dominant STT latency contributor; STT finalization p50 reduced below 800ms while maintaining command WER < 10%.
- **Verification Method**: Benchmark run via `tests/benchmarks/voice/benchmark_stt_tuning.py`.
- **Dependencies**: Working GPU/CPU STT provider.
- **Risks**: Alternative models may have higher error rates on numbers, names, or short command phrases.

#### VOICE-004: Kokoro TTS First-Audio Synthesis Optimization
- **Priority**: P1
- **Status**: NOT_IMPLEMENTED
- **Evidence**: `PROGRESS.md` Phase V2 P1.d (`FAIL — target missed: 1366.1ms vs 400ms target`).
- **Problem**: Kokoro ONNX on CPU requires ~1.36s to synthesize the initial sentence chunk, introducing a noticeable delay before audio playback begins.
- **Required Work**:
  1. Benchmark ONNX Runtime execution providers (DirectML, TensorRT, or CUDA) for Kokoro synthesis on Windows.
  2. Implement aggressive early chunking in `SentenceStreamer` (emit first speakable chunk at 5–7 words) to begin synthesis earlier during LLM token streaming.
  3. Pre-warm Kokoro model session with dummy phoneme sequences during TOM startup.
- **Acceptance Criteria**: Time from `sentence1` token yield to first synthesized audio chunk p50 < 450ms.
- **Verification Method**: Telemetry report marks (`sentence1` to `tts_first_synth_end`) in `logs/tom.log`.
- **Dependencies**: `SentenceStreamer`, `StreamingTTSProvider`.
- **Risks**: GPU execution for TTS consumes VRAM (~300–400MB) that must be accounted for in `ResourceManager`.

#### VOICE-005: Commit & Merge Working-Tree Wake-Word UX Polish
- **Priority**: P1 (Release Decision)
- **Status**: DONE_VERIFIED
- **Commit**: `79e5362` on `main` — `feat(voice+core): VOICE-005, CORE-004, TRACK-C, RESOURCE-001`
- **Verification**: `pytest tests/unit/voice/test_wakeword.py` — 15/15 passed; `git status` clean; pushed to `origin/main`.

#### VOICE-006: Smart Turn V3 Accuracy Resolution or Clean Deprecation
- **Priority**: P2
- **Status**: UNDER_REVISION
- **Evidence**: `PROGRESS.md` line 238; `HANDOFF.md` line 186 (100% false-complete rate on hesitation pauses).
- **Problem**: Smart Turn V3 cannot serve as a reliable endpointer because it prematurely closes turns during natural conversational pauses (0/15 correct on hesitation prefixes).
- **Required Work**: Evaluate whether to (a) retain Smart Turn by retraining/re-tuning with speech hangover corpora, or (b) formally deprecate the optional Pipecat dependency, document pure VAD hangover (600ms) as the authoritative endpointer, and remove dead fallback arbitration paths.
- **Acceptance Criteria**: Formal ADR documenting whether Smart Turn is retained or deprecated; zero false completions during 600ms mid-sentence hesitations.
- **Verification Method**: Run `tests/benchmarks/voice/ws3/smart_turn_eval.py`.
- **Dependencies**: Audio test corpus.
- **Risks**: Retaining unused optional dependencies increases packaging and maintenance complexity.

---

### Workstream 2: CORE (System Lifecycle, Context & History)

#### CORE-001: Graceful Capability-Specific Readiness & Startup Probes
- **Priority**: P1
- **Status**: PARTIAL
- **Evidence**: `python/tom/main.py:L699`; `python/tom/core/lifecycle.py:L145`.
- **Problem**: `LifecycleManager` verifies Rust engine connection, but does not verify local LLM server availability during normal startup. When `llama.cpp` is offline, TOM boots silently into voice mode and fails when speech is transcribed. Conversely, an offline LLM should not prevent non-LLM operations (system health checks, telemetry, audio device testing).
- **Required Work**:
  1. Implement capability-specific probes in `LifecycleManager`:
     - *Engine Service*: Named pipe ping & status (blocking for voice/desktop).
     - *Inference Service*: HTTP GET `/v1/models` probe to `127.0.0.1:65400`.
     - *Audio Hardware*: Microphone capture & playback device availability.
  2. Implement graceful degradation: if `llama.cpp` is offline, report degraded status, allow CLI/diagnostics, and notify the user via console/audio that inference is unavailable rather than crashing unhandled.
- **Acceptance Criteria**: Startup outputs clear status per subsystem; offline LLM results in an informative warning and degraded mode rather than an unhandled runtime exception.
- **Verification Method**: Unit test in `tests/unit/core/test_lifecycle.py` simulating offline inference endpoint.
- **Dependencies**: `LifecycleManager`, `HttpModelProvider`.
- **Risks**: None.

#### CORE-002: Repository Full-Suite Test Baseline & Regression Sweep
- **Priority**: P1 (Release Validation)
- **Status**: DONE_VERIFIED
- **Commit**: `79e5362` — full unit suite ran clean (exit 0, 8 optional skips for opencv/winocr).
- **Evidence**: `pytest tests/unit -q --timeout=30` — all tests pass. Integration and Cargo sweeps remain pending.

#### CORE-003: Persistent Session State & Resumable Context Schema
- **Priority**: P2 (Architecture Decision)
- **Status**: NOT_IMPLEMENTED
- **Evidence**: `IMPLEMENTATIONPLAN.md Phase 5`; `python/tom/core/context.py`.
- **Problem**: TOM currently instantiates ephemeral session IDs per launch. If the application is restarted, all conversational context, entity referents, and long-horizon task progress are wiped from RAM.
- **Required Work**:
  1. Architect and document the separation between *ephemeral conversational dialogue* (which may be discarded for privacy) and *resumable task execution state* (which must survive restarts).
  2. Create a SQLite schema (`sessions`, `task_checkpoints`) storing active task DAGs, intermediate results, and referents.
  3. Add `--resume-session <id>` CLI flag allowing continuation of interrupted background workflows.
- **Acceptance Criteria**: Documented ADR in `docs/design/` defining session persistence boundaries; interrupted multi-step tasks can be inspected and resumed after application restart.
- **Verification Method**: Integration test simulating crash and restart during multi-step execution.
- **Dependencies**: `SQLiteMemoryStore`, `TaskManager`.
- **Risks**: Stale referents (e.g. `last_screen`) may no longer match physical OS state after restart.

#### CORE-004: Canonical Structured Conversation History & Recall Verification
- **Priority**: P1
- **Status**: DONE_VERIFIED
- **Commit**: `79e5362` — `schemas/agent.py` gains token budgeting, atomic trim, tool-pair preservation; `tests/unit/agents/test_conversation_recall.py` 5/5 pass.
- **Verification**: `pytest tests/unit/agents/test_conversation_recall.py` — 5/5 passed in 0.55s.

---

### Workstream 3: TASK (Unified Task Lifecycle & Cancellation)

#### TASK-001: Task Registry In-Memory Eviction & Pruning Policy
- **Priority**: P2
- **Status**: PARTIAL
- **Evidence**: `python/tom/agents/task_manager.py:L60` (`_registry: dict[str, tuple[Task, CancellationToken]]`).
- **Problem**: `TaskManager` registers all tasks in a monotonically growing dictionary. Completed, failed, and cancelled tasks are never purged, creating a slow memory leak in long-running background daemon mode.
- **Required Work**: Implement an explicit task retention and eviction policy in `TaskManager` (e.g. retain at most 100 finished tasks, or prune tasks older than 24 hours). Add a periodic `prune_completed_tasks()` maintenance routine.
- **Acceptance Criteria**: Registry size remains bounded regardless of task execution count; unit tests confirm memory reclamation.
- **Verification Method**: Unit test in `tests/unit/agents/test_task_manager.py` verifying pruning after threshold.
- **Dependencies**: None.
- **Risks**: None.

#### TASK-002: Link Long-Running Workflows to TaskManager Lifecycle
- **Priority**: P2
- **Status**: NEEDS_REVIEW
- **Evidence**: `python/tom/agents/task_manager.py:L45`; `python/tom/voice/turn_manager.py:L109`.
- **Problem**: Ordinary conversational voice queries do not need the overhead of full task lifecycle tracking. However, actionable long-running workflows (multi-step file processing, web research, scheduled reminders) must link to `TaskManager` to enable cancellation, progress reporting, and status queries.
- **Required Work**: Ensure ordinary voice turns execute lightweight via `ConversationTurnManager`, while multi-step goals spawned from voice or proactive triggers instantiate a tracked `Task` with unique `task_id` in `TaskManager`.
- **Acceptance Criteria**: Multi-step plans appear in `TaskManager.list_tasks()` with real-time state transitions; ordinary single-turn voice responses bypass task registry.
- **Verification Method**: Integration test verifying distinction between immediate conversational turns and multi-step tasks.
- **Dependencies**: `AgentOrchestrator`, `TaskManager`.
- **Risks**: None.

#### TASK-003: End-to-End Cancellation Propagation
- **Priority**: P1
- **Status**: PARTIAL
- **Evidence**: `python/tom/core/context.py` (`CancellationToken`); `python/tom/agents/orchestrator.py:L195`; `python/tom/resources/manager.py:L180`.
- **Problem**: When a user cancels an in-flight operation (via voice barge-in, hotkey `Ctrl+Alt+Shift+Escape`, or UI), cancellation tokens are signaled, but audit evidence is needed to prove that: (1) no subsequent agent planning steps or tool calls execute, (2) in-flight HTTP web fetches or LLM stream generators cleanly close, (3) partial file writes or database transactions roll back or clean up safely, and (4) acquired resource leases (VRAM/RAM) in `ResourceManager` are immediately released.
- **Required Work**:
  1. *Audit Call Sites*: Verify cancellation token polling across `AgentOrchestrator.step()`, `ToolExecutor.execute()`, `WebFetchProvider`, and `VisionManager.analyze()`.
  2. *Enforce Post-Cancellation Gate*: Assert that `ToolExecutor` refuses to dispatch new tools once the token is cancelled.
  3. *Resource Lease Guarantee*: Verify that `ResourceManager.acquire()` contexts cleanly release VRAM reservations if cancelled during or immediately following grant (boundary test).
  4. *Partial-Operation Semantics*: Ensure file tools do not leave corrupt truncated files on disk when cancelled mid-write (atomic write via temp file + rename).
- **Acceptance Criteria**: Cancellation terminates active generation in <100ms; zero subsequent tool invocations occur; zero resource leases remain leaked; zero corrupted temporary files persist.
- **Verification Method**: Adversarial cancellation tests in `tests/integration/test_cancellation_pipeline.py`.
- **Dependencies**: `CancellationToken`, `ResourceManager`, `ToolExecutor`.
- **Risks**: Network sockets in `httpx` or external C++ threads in `llama.cpp` may require explicit timeout handling during cancellation.

---

### Workstream 4: AGENT (Multi-Step Planning & Self-Correction)

#### AGENT-001: Live Multi-Step Planning & Tool Execution Verification
- **Priority**: P2
- **Status**: IMPLEMENTED_UNVERIFIED
- **Evidence**: `python/tom/agents/orchestrator.py:L38`; `python/tom/agents/planner.py`; `python/tom/agents/loop_detector.py`.
- **Problem**: Multi-step planning, sub-task DAG creation, and tool-loop detection have extensive unit tests with mocked LLM outputs, but live multi-step execution using the real local Qwen2.5-7B model has not been benchmarked.
- **Required Work**: Author an end-to-end integration test executing a realistic multi-step user goal (e.g. "Find all Python files in directory X modified today, check their line counts, and summarize the result in a new file") against the live local LLM.
- **Acceptance Criteria**: The goal executes to completion within the 10-step bound, synthesizing intermediate tool results into a coherent final response without infinite loops.
- **Verification Method**: Test script in `tests/integration/agents/test_live_agent_planning.py`.
- **Dependencies**: Running local `llama.cpp` with Qwen2.5-7B.
- **Risks**: Smaller local 7B models may occasionally output malformed JSON arguments, testing error recovery loops.

---

### Workstream 5: MEM (Memory Manager & Persistence)

#### MEM-001: SQLite/Qdrant Consistency, Dual-Delete & Stale-Vector Handling
- **Priority**: P2
- **Status**: IMPLEMENTED_UNVERIFIED
- **Evidence**: `python/tom/memory/manager.py:L108, L127–137`; `python/tom/memory/sqlite.py`; `python/tom/memory/qdrant.py`.
- **Problem**: `MemoryManager` coordinates authoritative storage in SQLite and semantic indexing in Qdrant. If Qdrant is unavailable, updates or deletions may succeed in SQLite but fail in Qdrant, resulting in stale vector entries that return phantom results on future semantic queries.
- **Required Work**:
  1. Add synchronization verification: when updating or deleting a memory record, verify behavior under Qdrant failure.
  2. Implement a reconciliation check or tombstone mechanism: if vector deletion fails, mark the record in SQLite so subsequent vector hits confirm existence in SQLite before returning to the caller.
  3. Author unit tests covering: Qdrant completely offline, Qdrant disconnect during delete, and stale vector filtering.
- **Acceptance Criteria**: Deleting a record guarantees it is never returned by hybrid search even if Qdrant fails to delete the vector; SQLite remains 100% authoritative.
- **Verification Method**: Unit tests in `tests/unit/memory/test_memory_manager.py`.
- **Dependencies**: `MemoryManager`.
- **Risks**: None.

#### MEM-002: User Memory Controls & Inspection Tool Coverage
- **Priority**: P2
- **Status**: IMPLEMENTED
- **Evidence**: `python/tom/tools/memory.py` (5 tools: `remember`, `recall`, `forget`, `list_memories`, `clear_memories`).
- **Problem**: Users need explicit visibility and control over what facts TOM has stored about them, with granular inspection and selective deletion.
- **Required Work**: Verify that memory tools allow filtering by category/tag, inspect raw stored text, and allow deleting specific memory IDs via voice or text prompts.
- **Acceptance Criteria**: User can ask "What do you know about my preferences?" and "Forget my home address", resulting in accurate retrieval and deletion.
- **Verification Method**: Integration test in `tests/integration/memory/test_memory_pipeline.py`.
- **Dependencies**: `ToolExecutor`, `MemoryManager`.
- **Risks**: None.

---

### Workstream 6: TOOL (Tool Registry, Execution & Grounding)

#### TOOL-001: SearXNG Dependency Graceful Degradation & Documentation
- **Priority**: P1
- **Status**: NOT_IMPLEMENTED
- **Evidence**: `python/tom/tools/web.py:L144` (`SearXNGSearchProvider`); `IMPLEMENTATIONPLAN.md Phase 10`.
- **Problem**: `web.search` requires a self-hosted SearXNG instance. If SearXNG is offline, web search fails with connection errors. Crucially, an offline SearXNG instance must not break or disable unrelated local tools (system, files, memory, vision).
- **Required Work**:
  1. Ensure `web.search` failure is contained: return `ToolResult(error="Local web search service (SearXNG) is unreachable. Run SearXNG or use direct URLs with web.fetch.")`.
  2. Document SearXNG installation, configuration, and Docker setup in `docs/tools/web.md`.
  3. In `--health` diagnostics, report SearXNG as an optional service rather than a fatal system failure.
- **Acceptance Criteria**: Disabling SearXNG does not impair file, system, or vision tools; `web.search` returns a descriptive, graceful error message.
- **Verification Method**: Unit test in `tests/unit/tools/test_web_search.py` with mock connection error.
- **Dependencies**: None.
- **Risks**: None.

#### TOOL-002: Tool Result Schema Export & Grounding Anti-Fabrication Audit
- **Priority**: P2
- **Status**: DONE_VERIFIED
- **Evidence**: `python/tom/tools/registry.py`; `tests/unit/tools/test_executor.py` (`TestFabricatedToolResultsPrevention`).
- **Problem**: LLMs may hallucinate tool execution results or invent system metrics when tools are denied or fail.
- **Required Work**: Maintain and verify strict anti-fabrication prompt constraints in `VOICE_SYSTEM_PROMPT` and `CLI_SYSTEM_PROMPT` ensuring failed or denied tools are reported truthfully to the user.
- **Acceptance Criteria**: Regression tests confirm model explicitly states tool failure instead of inventing outputs.
- **Verification Method**: `pytest tests/unit/tools/test_executor.py::TestFabricatedToolResultsPrevention`.
- **Dependencies**: `ToolExecutor`.
- **Risks**: None.

---

### Workstream 7: VISION (Capture, OCR, CV & VLM)

#### VISION-001: Ephemeral Screen Capture & Multi-Monitor Coordinate Verification
- **Priority**: P2
- **Status**: IMPLEMENTED_UNVERIFIED
- **Evidence**: `python/tom/vision/capture.py:L70`; `python/tom/vision/privacy.py`.
- **Problem**: Multi-monitor desktop geometry, negative virtual screen offsets, and Windows 11 DPI scaling factors (125%, 150%) have not been verified across physical multi-monitor setups to ensure coordinate alignment during element location.
- **Required Work**: Validate that `ScreenCaptureService` properly handles multi-monitor bounding boxes, normalizes coordinates to physical pixels, and ensures zero screenshot bytes touch disk (ephemeral in-memory PIL/bytes processing only).
- **Acceptance Criteria**: Screen crops on secondary monitors align with visual elements; zero image files written to filesystem.
- **Verification Method**: Unit tests with mocked multi-monitor geometry in `tests/unit/vision/test_capture.py`.
- **Dependencies**: `ScreenCaptureService`, `PrivacyShield`.
- **Risks**: DPI scaling mismatches between monitors can cause coordinate misalignment if uncorrected.

#### VISION-002: Local VLM Inference, Model Availability & VRAM Arbitration Verification
- **Priority**: P2
- **Status**: PARTIAL
- **Evidence**: `python/tom/vision/vlm.py`; `python/tom/resources/manager.py:L140` (`ModelRole.VLM`); `config/models.yaml`.
- **Problem**: While `VisionManager.analyze()` implements capability routing (OCR → CV → VLM) and acquires `ModelRole.VLM` through `ResourceManager`, end-to-end execution of actual local VLM inference (e.g. Qwen2.5-VL-7B) has not been verified against local model weights under strict 7.2GB VRAM limits.
- **Required Work**:
  1. Verify physical existence of VLM model weights during startup probe; gracefully fall back to OCR/CV if absent.
  2. Test mutual exclusion in `ResourceManager`: verify that reasoning LLM leases are suspended or evicted before VLM inference loads, preventing GPU Out-Of-Memory (OOM) errors.
  3. Measure VLM inference latency and memory footprint on RTX 4060 GPU.
- **Acceptance Criteria**: VLM inference executes on local GPU without exceeding 7.2GB total VRAM allocation; mutual exclusion guarantees no concurrent heavy LLM allocation; missing model weights fall back cleanly to OCR/CV.
- **Verification Method**: Integration test in `tests/integration/vision/test_vision_vlm_resource.py`.
- **Dependencies**: `ResourceManager`, `VisionManager`, local VLM model file.
- **Risks**: VLM weight size (~5.5GB) leaves very tight headroom on an 8GB GPU.

---

### Workstream 8: SEC (Security, Permissions & Confirmation)

#### SEC-001: Spoken Interactive Voice Confirmation Protocol
- **Priority**: P1
- **Status**: NOT_IMPLEMENTED
- **Evidence**: `python/tom/security/confirmation.py:L256` (`ConsoleConfirmationHook.isatty() == False` returns `False` immediately); `python/tom/voice/interaction_core.py`.
- **Problem**: In voice mode, `sys.stdin.isatty()` is False. Consequently, all mutating actions classified as `ASK_USER` (file writes, calendar updates, reminder creation, process termination) auto-deny by fail-closed design. The user is left unable to execute actions hands-free.
- **Required Work**:
  1. *Operation-Bound Confirmation Token*: When an `ASK_USER` tool executes in voice mode, generate a short-lived cryptographic confirmation token bound strictly to the tool name, canonical parameters, and session ID.
  2. *Spoken Clarification Prompt*: TOM synthesizes a concise, unambiguous prompt: "You asked to write to file report.txt. Please confirm by saying yes or cancel."
  3. *Time-Bounded Arming*: Arm a 6.0-second confirmation listening window. Speech outside this window or any response other than an explicit affirmation ("yes", "confirm", "proceed") fails closed to denial.
  4. *Acoustic Echo Protection*: The confirmation listener must not open until TTS playback of the prompt has fully ceased, preventing assistant speech bleed from auto-confirming the action.
  5. *Tiered Security Policy*: Define explicit boundaries:
     - *Voice-Confirmable*: Safe mutations (personal notes, reminders, local workspace file writes).
     - *Terminal/GUI-Only*: High-risk operations (executing shell scripts, deleting system directories, credential access) must NEVER be confirmable by voice and must require interactive terminal confirmation.
- **Acceptance Criteria**: Voice mutation prompts for confirmation, executes upon spoken "yes", rejects unconfirmed utterances, and strictly blocks high-risk operations from voice confirmation.
- **Verification Method**: Hermetic unit and integration tests in `tests/unit/security/test_voice_confirmation.py`.
- **Dependencies**: `PermissionEngine`, `InteractionCore`, `ConsoleConfirmationHook`.
- **Risks**: Acoustic false-positives from room background speech must be strictly prevented via explicit confirmation phrase matching.

---

### Workstream 9: RESOURCE (Resource Governance & Memory Budgets)

#### RESOURCE-001: Authoritative STT Device Configuration Alignment
- **Priority**: P1
- **Status**: DONE_VERIFIED
- **Commit**: `79e5362` — `whisper_on_gpu` removed from `config/resources.yaml` and `ResourcesConfig` schema; `models.yaml` is now sole authority.
- **Verification**: `pytest tests/unit/core/test_config.py` — 7/7 passed.

---

### Workstream 10: OBS (Observability, Telemetry & Health Checks)

#### OBS-001: Structured Health Check CLI with Required vs. Optional Status
- **Priority**: P2
- **Status**: PARTIAL
- **Evidence**: `scripts/health_check.py`; `python/tom/main.py:L377`.
- **Problem**: The existing health check reports basic Named Pipe and engine status, but does not provide visibility into whether individual capabilities (LLM inference, STT/TTS models, SearXNG, audio capture) are ready, nor does it distinguish between *required core dependencies* and *optional auxiliary services*.
- **Required Work**:
  Extend `scripts/health_check.py` to output a structured capability report:
  - **Required Core**: Rust Engine IPC, Audio Capture Device, Audio Playback Device, LLM Inference Endpoint.
  - **Optional Capabilities**: Local SearXNG (web search), Qdrant Vector Index (semantic memory), Local VLM Weights (advanced vision).
  Return exit code 0 if all required services pass (even if optional services are offline), and exit code 1 if any required service fails.
- **Acceptance Criteria**: Running `python -m tom.main --health` outputs a clear, colorized report separating required vs. optional health status with accurate exit codes.
- **Verification Method**: Run `python -m tom.main --health` in terminal.
- **Dependencies**: None.
- **Risks**: None.

---

### Workstream 11: UX (User Experience, Desktop & Interaction)

#### UX-001: Visual & Auditory Readiness State Feedback
- **Priority**: P1
- **Status**: PARTIAL
- **Evidence**: `python/tom/voice/interaction_core.py:L791` (`startup_greeting=False` by default); `python/tom/desktop/tray.py`.
- **Problem**: In silent-startup voice mode, TOM initializes models and connects to audio devices over 2–4 seconds without providing any external indicator of when it is ready to receive the wake word. The user cannot tell whether the system is ready, loading, or hung.
- **Required Work**:
  1. Update system tray icon tooltip dynamically: "TOM — Starting...", "TOM — Ready (Listening)", "TOM — Busy", "TOM — Sleeping".
  2. Implement an optional subtle, non-intrusive auditory earcon (a clean 40ms 880Hz chime) played upon first transition into `LISTENING`.
  3. Ensure the earcon playback does not trigger the microphone VAD.
- **Acceptance Criteria**: User receives immediate feedback when the microphone becomes armed and listening.
- **Verification Method**: Manual inspection during desktop startup.
- **Dependencies**: `TrayDaemon`, `InteractionCore`.
- **Risks**: Auditory chime must be short and properly isolated from capture ingestion.

---

### Workstream 12: BENCH (Benchmarking & Long-Session Reliability)

#### BENCH-002: 50-Cycle Continuous Concurrency & Stability Soak Test
- **Priority**: P3
- **Status**: NOT_IMPLEMENTED
- **Evidence**: `IMPLEMENTATIONPLAN.md Phase 12 Exit Criteria`; `tests/unit/resources/test_concurrency_stress.py`.
- **Problem**: Short test runs do not catch slow memory leaks, named-pipe handle exhaustion, thread starvation, or VRAM fragmentation that occur over hours of background desktop execution.
- **Required Work**: Implement an automated soak test harness simulating 50 consecutive interactive turns (voice audio input → LLM streaming → tool execution → TTS playback) using synthetic PCM fixtures. Track system RAM, VRAM, and task counts after each cycle.
- **Acceptance Criteria**: 50 complete cycles execute without OOM, without thread leakage, and with zero unreleased resource leases in `ResourceManager`.
- **Verification Method**: Script execution via `pytest tests/benchmarks/test_stability_soak.py`.
- **Dependencies**: `ResourceManager`, `InteractionCore`.
- **Risks**: Requires ~20 minutes of continuous local hardware execution.

---

## 6. Dependency Graph & Execution Sequence

```
[VOICE-005: Commit/Merge Wake-Word UX]
       │
       ▼
[CORE-002: Full-Repo Test Baseline Sweep]
       │
       ▼
[CORE-001: Graceful Probes & Degradation] ──► [RESOURCE-001: STT Config Source Alignment]
       │
       ▼
[CORE-004: Canonical History & Recall] ──► [TASK-003: Cancellation Propagation]
       │
       ▼
[VOICE-001: Barge-In Interruption Path]
       │
       ▼
[BENCH-001: Live Baseline Measurement] ──► [VOICE-002: 15-Turn Human Acceptance Gate]
       │
       ▼
[SEC-001: Voice Confirmation Protocol] ──► [TOOL-001: SearXNG Degradation]
       │
       ▼
[VOICE-003: STT Pipeline Latency] ──► [VOICE-004: Kokoro TTS Optimization]
```

---

## 7. Recommended First 5 Engineering Tasks

1. **VOICE-005 (Commit Wake-Word UX Polish)**: Cleanly commit the 11 modified working-tree files on branch `wip/audit-2026-11-02` after running passing wake-word unit tests.
2. **CORE-002 (Full-Suite Test Baseline Sweep)**: Run all 96 unit test files and integration suites outside the sandbox to confirm current passing baseline and catch regressions.
3. **RESOURCE-001 (Authoritative STT Config Alignment)**: Remove misleading `whisper_on_gpu` from `config/resources.yaml` and designate `models.yaml` as the sole authority.
4. **CORE-001 (Graceful Capability Probes)**: Add upfront HTTP reachability check to `LifecycleManager` for `llama.cpp` with graceful degradation.
5. **CORE-004 (Canonical Conversation History & Recall)**: Consolidate `Agent._history` and `ConversationTurnManager._history` to resolve nested history duplication and pass 12-turn recall tests.

---

## 8. Ponytail-Audit Findings (Simplification & Pruning Guidance)

> [!CAUTION]
> In accordance with user rules, suspected simplifications must **not** be turned into automatic deletions. Every item below requires concrete runtime profiling, measurable performance/line-count benefits, and full regression testing before any abstraction or safety boundary is removed.

1. **`python/tom/voice/formatter.py` (`SpeechFormatter`)**:
   - *Observation*: Contains 6 sequential regex substitution passes.
   - *Guidance*: Retain for now. Profile CPU overhead during live streaming. Only simplify if regex passes contribute >5ms to perceived turnaround.
2. **`python/tom/voice/response_policy.py` (`ResponsePolicy`)**:
   - *Observation*: Defines 7 discrete response actions (`SILENT_ACT`, `CLARIFY`, `FAST_ACK_THEN_WORK`), but `SPEAK_DIRECT` represents >95% of live turns.
   - *Guidance*: Keep intact. These branches are necessary safety boundaries for destructive actions and ambiguous user intents as multi-step agent tool use expands.
3. **`python/tom/memory/manager.py` (`MemoryManager.forget`)**:
   - *Observation*: One-line wrapper directly delegating to `delete()` with identical parameters.
   - *Guidance*: Low harm; keep for conversational semantics or prune during next major memory refactor.
4. **Benchmark Virtual Environments (`.venv-ws2-bench`, `.venv-ws3-bench`)**:
   - *Observation*: Left in repository root after WS2/WS3 evaluation rounds.
   - *Guidance*: Do not delete without user confirmation. They isolate conflicting versions of `sherpa-onnx` and `onnxruntime` needed for offline benchmark reproducibility.

---

## 9. Definition of Done for Backlog Tasks

A task in this backlog is marked `DONE_VERIFIED` only when:
1. Code modifications are implemented adhering strictly to repository architecture and security invariants.
2. Unit tests covering nominal execution, error handling, and cancellation pass hermetically.
3. Appropriate structured telemetry events are emitted with latency/outcome metrics.
4. Documentation in `STATE.md`, `PROGRESS.md`, and `HANDOFF.md` is updated.
5. Repository quality checks (`ruff check`, `ruff format --check`, `cargo test`) run 100% clean with zero warnings or errors.

