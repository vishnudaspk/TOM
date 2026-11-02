# TOM — Comprehensive Repository Audit & Architecture Review

**Date:** 2026-11-02  
**Auditor:** Antigravity (Advanced Agentic Assistant)  
**Methodology:** Direct AST & Code Inspection, Monotonic Latency Trace Analysis, Graphify Knowledge Graph Context, Ponytail Audit for Over-Engineering  
**Baseline Git Commit:** `7d0440f` ("Update README: reflect current status, architecture, hurdles, and roadmap") with 21 modified working-tree files  

---

## A. Executive Summary

### 1. Where TOM Actually Stands
TOM (The Operating Mind) is a local-first, dual-core AI operating layer combining a high-performance **Rust engine (`tom-engine`)** for OS telemetry, audio capture/playback, and input injection, with a **Python brain** for AI orchestration, tool execution, memory, and conversation management.

Architecturally, Phases 0–8, Phase 10 (Web & Personal Tools), Phase 11 (Security Hardening), Phase 12 (Concurrency & Resource Governance), Phase 13 (IPC Stabilization), and Phase 14 (Desktop System Tray) are solidly implemented in code with exhaustive unit and adversarial test suites.

However, **TOM is NOT yet production-ready for live, hands-free conversational voice interaction**. The voice pipeline is currently in a hybrid state where individual components (VAD, Streaming STT, Orchestrator streaming, Streaming TTS, Turn Manager, Response Policy) work in isolation and against synthetic mocks, but the live end-to-end event loop (`InteractionCore`) suffers from architectural gaps, latency bottlenecks, and critical logic bugs in gating and barge-in.

### 2. What Works
- **Dual-Core IPC Foundation:** Rust `tom-engine` with Tokio, length-prefixed JSON protocol over Windows Named Pipe `\\.\pipe\tom-engine`, robust retry cycles, and low-latency system telemetry.
- **34 Canonical Tools & Security Foundation:** Centralized `PathPolicy` filesystem containment, regex + Shannon entropy ($H \ge 4.5$) `SecretScrubber`, fail-safe `ConsoleConfirmationHook` with rate limits, append-only `SecurityAuditLogger`, and strict `BaseModel` preservation.
- **Resource Governor & Concurrency:** `ResourceManager` with 500 MB VRAM headroom, `PriorityResourceQueue` (TaskPriority 0–5 with starvation aging and cooperative preemption), `ModelLifecycleCoordinator` state machine, and zero-drift stress testing.
- **Authoritative SQLite Memory:** Transactional persistence for memories, entities, notes, calendar events, and reminders with proactive scheduling integration.
- **Streaming Pipeline Architecture:** Token delta streaming from llama.cpp, `SentenceStreamer` boundary detection, `IncrementalTTSStreamer` sentence-pipelined synthesis with Kokoro-82M ONNX, and utterance-buffered audio IPC.
- **Desktop Integration:** System tray daemon (`TrayDaemon`) with connection status monitoring, microphone mute toggle, and CLI text console.

### 3. What Remains Unfinished & Broken
1. **Barge-in Blocked During SPEAKING (Critical Bug):** In `python/tom/voice/microphone.py`, `push_samples()` drops all incoming samples when `self._gated == True` (`if self._stopped or self._muted or self._gated: return`). Because `_on_speaking` sets `_mic.gate()`, the RMS threshold logic in `_process_frame` is dead code during playback. Barge-in during speech is impossible.
2. **Streaming Markdown Leakage into TTS:** `TurnExecutor._stream_response` feeds raw LLM token stream chunks directly into Kokoro TTS without passing them through `SpeechFormatter`. Asterisks, headers, bullets, and markdown symbols leak into synthesized voice output.
3. **Missing Whisper Hallucination Filtering:** Ambient noise, trailing audio, or silence fed to FasterWhisper causes characteristic Whisper hallucinations ("Thank you for watching!", "Subtitles by..."), with zero heuristic or pattern filtering in place.
4. **CUDA STT DLL Discovery Failure:** On Windows, `FasterWhisperSTTProvider` cannot discover `cublas64_12.dll` installed in `.venv` site-packages because `os.add_dll_directory` is never invoked. It fails with `cublas64_12.dll not found`, memoizes the failure, and permanently falls back to CPU.
5. **Inverted Latency Stage Calculation:** In `python/tom/voice/latency.py`, `_METRIC_STAGES["stt_queue_wait_ms"]` is defined as `(LatencyStage.STT_FINAL, LatencyStage.STT_DECODE_START)`. Because decode start precedes finalization, `stt_queue_wait_ms` evaluates to a negative number.
6. **Smart Turn Incomplete Utterance Hang:** `smart_turn_max_incomplete_wait_ms` is declared in configuration but never scheduled in `InteractionCore`. If Smart Turn returns `CONTINUING` and the user stops speaking, the turn hangs indefinitely.
7. **Severe Real-World Latency:** Live hardware benchmarks (13 turns) show warm median `perceived_response_ms` at **6469 ms** (target: 1500 ms, ceiling: 2200 ms). STT decode on CPU alone accounts for ~1734–3484 ms.

---

## B. Architecture Audit

### 1. Dual-Core Process Model & Data Flow

```text
[ Physical Input / OS ]
           │
           ▼
┌────────────────────────────────────────────────────────┐
│                   Rust tom-engine                      │
│  - AudioManager (CPAL 16kHz capture ring buffer)       │
│  - Playback Stream (CPAL 24kHz audio output)           │
│  - InputManager (Win32 SendInput, clamp, cancel hotkey)│
│  - System Probes (CPU, RAM, GPU, Battery, Processes)   │
│  - Named Pipe IPC Server: \\.\pipe\tom-engine          │
└──────────────────────────┬─────────────────────────────┘
                           │ Length-Prefixed JSON IPC
                           ▼
┌────────────────────────────────────────────────────────┐
│                   Python TOM Brain                     │
│                                                        │
│  [Audio & Voice Pipeline]                              │
│   MicrophoneLoop (20ms poll, Energy VAD)               │
│          │                                             │
│          ▼ SpeechEnd                                   │
│   InteractionCore ──> SmartTurnV3Arbiter (Optional)    │
│          │                                             │
│          ▼ Committed Audio                             │
│   TurnExecutor ──> FasterWhisper STT (CPU / CUDA)      │
│          │                                             │
│          ▼ User Text                                   │
│   ConversationTurnManager + ResponsePolicy             │
│          │                                             │
│          ▼ Prompt Stream                               │
│   AgentOrchestrator ──> HttpModelProvider (llama.cpp)  │
│          │ Token Generator                             │
│          ▼                                             │
│   SentenceStreamer (Punctuation boundary chunking)     │
│          │                                             │
│          ▼ Speakable Chunks                            │
│   StreamingTTSProvider (Kokoro-82M ONNX)               │
│          │ Audio Chunks                                │
│          ▼                                             │
│   IncrementalTTSStreamer ──> EngineClient Playback     │
│                                                        │
│  [Core AI & Resource Subsystems]                       │
│   - ToolExecutor + PermissionEngine (34 tools)         │
│   - SecretScrubber + PathPolicy + SecurityAuditLogger  │
│   - ResourceManager + PriorityResourceQueue            │
│   - MemoryManager (SQLite authority + Qdrant index)    │
│   - TaskManager + TaskPlanner + TaskExecutor           │
│   - Desktop TrayDaemon + CLI Console                   │
└────────────────────────────────────────────────────────┘
```

### 2. Differences from Original Implementation Plan
- **IPC Audio Transport:** `IMPLEMENTATIONPLAN.md` assumed streaming binary PCM pipe. In reality (Decision 041), IPC is **utterance-buffered**: Python polls `audio.get_speech` every 20ms and submits synthesized speech in discrete buffers via `audio.play_buffer`.
- **VAD Architecture:** Original plan assumed Silero VAD or Rust-native VAD. In reality, VAD is pure **Python energy-based RMS VAD** (`EnergyVADDetector`), avoiding C-dependencies.
- **Inference Runtime:** Original plan referenced llama-cpp-python / vLLM. In reality, TOM standardizes on a **separate local llama.cpp server** accessed via `HttpModelProvider` at `http://127.0.0.1:65400/v1`.
- **Single Resident Model Invariant:** Original plan envisioned dynamic concurrent models; hardware realities (RTX 4060 8GB VRAM) enforce a strict **one large model in VRAM** invariant (ADR 054, ADR 059).
- **Smart Turn Advisory Status:** Smart Turn V3 was found to produce a 100% false-complete error rate on hesitation pauses; it was converted from an authoritative endpointer to an optional, advisory arbiter with pure VAD fallback.

### 3. Graphify Structural Findings
- Graph size: **8,989 nodes · 21,548 edges · 442 communities**.
- **God Nodes (Top Load-Bearing Abstractions):**
  1. `ToolRegistry` (308 connections) — Central hub of tool cataloging and schemas.
  2. `ToolExecutor` (185 connections) — Execution, scrubbing, and policy validation gate.
  3. `NamedPipeIpcClient` (119 connections) — Foundation of cross-language transport.
  4. `MockModelProvider` (118 connections) — Ubiquitous offline testing harness.
  5. `EngineClient` (113 connections) — High-level Rust IPC client API.
  6. `ResourceManager` (113 connections) — Hardware budget and concurrency gate.
  7. `CancellationToken` (109 connections) — Cooperative task and pipeline preemption.
  8. `PermissionEngine` (109 connections) — Data-driven tool security evaluation.
  9. `PersonalStore` (108 connections) — Relational store for user records.
  10. `ToolDefinition` (101 connections) — Pydantic schema contract for tools.
- **Structural Concerns:**
  - High coupling around `InteractionCore`: It directly wires together `MicrophoneLoop`, `VoicePipelineManager`, `TurnExecutor`, `ConversationTurnManager`, `ResponsePolicy`, `StreamingSTTProvider`, `StreamingTTSProvider`, `EngineClient`, and `TrayDaemon`.
  - Disconnect between `VoicePipelineManager` (Phase 6 legacy monolithic pipeline) and `InteractionCore` (Phase 9 streaming loop): both maintain overlapping state representations (`VoicePipelineState`), creating dual-state maintenance complexity.

---

## C. Subsystem Implementation Matrix

| # | Subsystem | Status | Primary Source Location | Test Evidence | Gaps & Debt | Next Action |
|---|---|---|---|---|---|---|
| **1** | Python Core, Lifecycle & Orchestration | **Implemented & Tested** | `python/tom/core/`, `python/tom/main.py`, `python/tom/agents/` | 55 core unit tests, 264 agent unit tests passing | `main.py` is oversized (644 lines); handles CLI, voice, tray, and signals in one class | Refactor `main.py` into modular run modes (`tom/cli/`, `tom/voice_mode/`) |
| **2** | Rust `tom-engine` Audio & IPC | **Implemented & Verified** | `rust/tom-engine/src/` | 100 Rust tests (93 unit, 7 integration/hardening) | Requires running background binary; CPAL capture lacks hardware/software AEC | Add echo cancellation or hardware loopback reference |
| **3** | IPC Protocol & Reconnection | **Implemented & Tested** | `python/tom/ipc/` | 161 IPC unit tests passing (6 reconnection cycle tests) | Audio IPC is polling-based (`get_captured_speech` 20ms timer); no binary stream | Keep utterance polling per Decision 041 |
| **4** | Tool Registry & Permissions | **Verified Complete** | `python/tom/tools/`, `python/tom/security/` | 230 tool unit tests, 38 pipeline integration tests | 34 canonical tools cleanly registered; schemas strictly exported | Maintain data-driven permissions invariant |
| **5** | LLM Provider & Local Inference | **Implemented & Tested (Mocks)** | `python/tom/models/providers/http.py`, `python/tom/models/base.py` | Unit tests with `MockModelProvider`; live tests require llama.cpp at :65400 | Multi-model residency swap pipeline (`STAGE3_DESIGN.md`) not automated | Build llama-server process lifecycle manager |
| **6** | Routing & Task Lifecycle | **Verified Complete** | `python/tom/core/router.py`, `python/tom/schemas/routing.py`, `python/tom/schemas/tasks.py` | Routing unit tests pass; `TaskState` machine validated | Intent classification relies on LLM heuristics when offline | Maintain fallback to main model |
| **7** | Agent & Planning System | **Verified Complete** | `python/tom/agents/`, `python/tom/agents/planner.py`, `executor.py` | 36 e2e agent integration tests passed | Context compression across long multi-step DAGs is basic | Add structured history summarization |
| **8** | Memory (SQLite & Qdrant) | **Verified Complete** | `python/tom/memory/` | 39 SQLite tests; hybrid search rank tests; Qdrant optional fallback verified | Qdrant is uninstalled in primary `.venv`; SQLite serves as ground truth | SQLite remains single source of truth |
| **9** | Voice Pipeline & Turn Taking | **Implemented but Incomplete / Bugs Present** | `python/tom/voice/` | 377 voice unit tests pass; real live sessions show 6469 ms latency | Mic gating blocks barge-in during playback; markdown leaks into TTS; negative wait metric; missing hallucination filter | Fix gating, wire `SpeechFormatter`, fix latency formula |
| **10** | Vision & OCR | **Implemented & Tested** | `python/tom/vision/`, `python/tom/tools/vision.py` | 26 unit tests; graceful skip for uninstalled `opencv-python`/`winocr` | `winocr` and `opencv-python` optional deps skipped | Maintain CPU-first and ephemeral RAM invariants |
| **11** | Web & Personal Productivity | **Verified Complete** | `python/tom/tools/web.py`, `personal.py`, `python/tom/personal/` | 8 web search, 27 web fetch, 71 personal unit tests pass | Web fetch relies on `httpcore` `sni_hostname` for DNS-pinning | Keep SSRF and IP pinning protections |
| **12** | Security Hardening & Isolation | **Verified Complete** | `python/tom/security/` | 101 adversarial security tests (traversal, secrets, injection) pass | Regex scanner throughput is ~19.8 MB/s; large payloads capped at 1 MiB | Keep strict fail-safe `***REDACTION_ERROR***` |
| **13** | GPU/RAM Resource Governor | **Verified Complete** | `python/tom/resources/` | 105 resource tests (19 adversarial stress tests, 100 cycles zero drift) | Software reservation limit does not enforce external physical VRAM | Retain 500 MB safety headroom margin |
| **14** | Telemetry, Tracing & Debug | **Implemented with Metric Bugs** | `python/tom/telemetry/`, `python/tom/voice/latency.py`, `debug_trace.py` | `InteractionLatencyTracer` tests pass; debug trace WAVs recorded | `stt_queue_wait_ms` formula inverted; tracer has no hallucination flag | Correct `_METRIC_STAGES` derivation |
| **15** | Desktop Tray & CLI Operation | **Verified Complete** | `python/tom/desktop/`, `python/tom/main.py` | 33 tray unit tests pass; text console verified | Headless environments must explicitly skip tray via `--headless` | Retain graceful degradation when pystray missing |

---

## D. Voice Reliability Assessment

### 1. Premature Endpointing During Long Utterances
- **Mechanism:** `MicrophoneLoop` tracks trailing silence frames. When `trailing_silence_ms >= config.silence_duration_ms` (600 ms), it emits `SpeechEndEvent`.
- **Findings:** A 600 ms hangover is too brief for natural human speech pauses (e.g. thinking, breathing, hesitating). In complex utterances, users frequently pause for 600–900 ms, causing the utterance to be cut in half.
- **Evidence:** Benchmark recordings in `tests/benchmarks/voice/ws3/recordings/` show that without Smart Turn arbitration, natural mid-sentence pauses trigger premature speech end.

### 2. Natural Pauses & Smart Turn Arbitration
- **Mechanism:** `InteractionCore._handle_speech_end` submits candidate PCM to `SmartTurnV3Arbiter.analyze()`. If decision is `CONTINUING`, audio is retained in `_smart_turn_candidate` and the turn is NOT committed.
- **Findings:** 
  1. Smart Turn V3 ONNX model was benchmarked across 97 clips in WS3. It exhibited a **100% false-complete error rate on hesitation pauses (0/15 correct)**. It falsely reported `COMPLETE` when the user hesitated.
  2. If the model does report `CONTINUING`, `_smart_turn_candidate` holds the audio indefinitely because **`smart_turn_max_incomplete_wait_ms` is never scheduled**. If the user does not resume speaking, the assistant never answers.
  3. Status remains **UNDER REVISION**. Pure VAD fallback with H=600ms is the active production behavior.

### 3. Barge-In During SPEAKING (Critical Bug)
- **Mechanism:** `_on_speaking` in `interaction_core.py` marks `self._playback_active = True` and calls `self._mic.gate()`. In `microphone.py`, `gate()` sets `self._gated = True`.
- **Defect:** In `MicrophoneLoop.push_samples()`:
  ```python
  if self._stopped or self._muted or self._gated:
      return
  ```
  Because `_gated` is `True`, `push_samples()` immediately drops all audio frames from the engine. The RMS calculation and barge-in threshold check in `_process_frame` (`if self._gated: if rms >= threshold...`) are **completely starved of data**.
- **Result:** **Barge-in during assistant speech is 100% inoperative.**

### 4. Interruption During PROCESSING
- **Mechanism:** When the pipeline is in `PROCESSING` (LLM inference or TTS synthesis before audio plays), `_handle_event` intercepts `SpeechStartEvent`:
  ```python
  if isinstance(event, SpeechStartEvent):
      if self._active_turn is not None and not self._active_turn.done():
          await self.handle_barge_in()
  ```
- **Findings:** Interruption during `PROCESSING` works correctly. `handle_barge_in()` cancels `_active_token`, bumps the epoch, cancels and reaps `_active_turn`, and resets state to `LISTENING`.

### 5. Playback Cancellation & Stale Queued Audio
- **Mechanism:** `PlaybackStopGuard` (`_stops.stop(epoch)`) calls `EngineClient.stop_audio_playback()` exactly once per epoch. In `IncrementalTTSStreamer`, worker generators are closed with `aclosing()`.
- **Findings:** Cleanly implemented. In-flight synthesis tasks are detached, pending synthesis queue is purged, and the Rust engine's playback buffer is flushed via IPC. Epoch checks prevent late audio chunks from being submitted.

### 6. Playback Completion & State Transitions
- **Mechanism:** `IncrementalTTSStreamer` added `playback_tail_ms` (default 400 ms) to allow the CPAL output buffer to finish draining before firing `on_playback_completed()`.
- **Findings:** Once playback finishes, `_mic.ungate()` is called and state transitions cleanly back to `LISTENING`.

### 7. Microphone Monitoring During Playback & Acoustic Echo
- **Mechanism:** Without AEC, the microphone picks up speaker audio during assistant output, causing false barge-in loops.
- **Current Workaround:** Complete mic gating (`self._mic.gate()`), which eliminates the feedback loop but completely disables barge-in.
- **Architectural Gap:** TOM lacks software Acoustic Echo Cancellation (WebRTC AEC3 or SpeexDSP).

### 8. Streaming Text Normalization & Markdown Leakage into TTS
- **Mechanism:** `SentenceStreamer` in `python/tom/voice/sentence_streamer.py` chunks tokens by punctuation.
- **Defect:** `TurnExecutor._stream_response` feeds chunks directly from the sentence queue to `StreamingTTSProvider.stream_synthesize()`.
- **Findings:** `SpeechFormatter` (which strips markdown, code blocks, bullet points, and expands symbols) is **never invoked** in the streaming voice pipeline. Kokoro TTS synthesizes raw markdown characters (e.g. saying "asterisk asterisk", "hash", or reading raw formatting).

### 9. Whisper Hallucination Filtering
- **Mechanism:** Whisper models are autoregressive and prone to hallucinating common phrases on silence or ambient noise.
- **Findings:** There is **zero hallucination filtering** in `stt.py` or `streaming_stt.py`. Repeated phrases, empty bracket sequences, or common YouTube dataset artifacts ("Thank you for watching") are passed directly to the LLM orchestrator as valid user speech.

### 10. CUDA DLL Discovery & Actual STT Execution Provider
- **Mechanism:** `FasterWhisperSTTProvider` configures `device: cuda` and `compute_type: int8_float16`.
- **Defect:** On Windows, CTranslate2 relies on Windows DLL search to load `cublas64_12.dll`. The wheel packages (`nvidia-cublas-cu12`) install DLLs into `.venv\Lib\site-packages\nvidia\cublas\bin`. Because `os.add_dll_directory` is never called, the DLL loader fails with `cublas64_12.dll not found`.
- **Fallback:** `_init_model_sync` catches the exception, logs a warning, sets `self._cuda_failed = True`, and falls back to CPU. Because `_cuda_failed` is memoized, all future STT runs stay on CPU.
- **Status:** **STT in TOM currently runs on CPU in practice**, unless the user manually modifies `$env:PATH` before launching.

### 11. Debug Tracing Infrastructure (`mic.wav`, `spk.wav`, `trace.jsonl`)
- **Implementation:** `python/tom/voice/debug_trace.py` provides `DebugTraceSession`. It writes 16 kHz mono `mic.wav`, 24 kHz mono `spk.wav`, and structured monotonic timeline `trace.jsonl`.
- **Validation Tools:** `python/tom/tools/check_trace.py` validates timeline continuity and WAV integrity; `tests/tools/replay_endpointing.py` provides offline VAD simulation.
- **Findings:** Fully implemented, functional, and enabled via `voice.debug_trace: true` in `config/voice.yaml`.

### 12. Latency Calculations & Negative `stt_queue_wait_ms`
- **Defect in `python/tom/voice/latency.py`:**
  Line 382:
  ```python
  "stt_queue_wait_ms": (LatencyStage.STT_FINAL, LatencyStage.STT_DECODE_START),
  ```
  `InteractionLatencyTracer._delta_ms(start, end)` calculates `(marks[end] - marks[start]) / 1e6`.
  Here `start = STT_FINAL` and `end = STT_DECODE_START`.
  Because `STT_DECODE_START` occurs *before* `STT_FINAL`, this calculation always yields a **negative millisecond value**.
- **Fix Required:** Define `stt_queue_wait_ms` as `(LatencyStage.SPEECH_END, LatencyStage.STT_DECODE_START)`.

### 13. Existing Voice Benchmarks & Validity
- **Fake vs. Real Runs:**
  - `tests/integration/voice/` tests use `ScriptedSTT`, `FlakyOrchestrator`, and `MockTTS` where all latencies evaluate to 0 ms (`docs/perf/00-invalid-fake-run.md`).
  - The live session documented in `docs/perf/00-baseline.md` is the only real hardware measurement:
    - `perceived_response_ms`: median 6469 ms (target 1500 ms) — **331% over ceiling**.
    - `stt_finalization_ms`: median 3484 ms (target 350 ms) — **596% over ceiling**.
    - `stt_decode_ms`: median 1734 ms (target 200 ms) — **333% over ceiling**.

---

## E. Hardware and Deployment Constraints

1. **Hardware Specification:** Windows 11, Intel i9-13900H, RTX 4060 Laptop (8GB physical VRAM, ~7000 MB practical VRAM budget), 16GB system RAM.
2. **Model Footprint on GPU:**
   - Reasoning LLM (Qwen2.5-7B Q4_K_M): ~4,531 MiB VRAM.
   - Desktop & OS display overhead: ~1,200–1,500 MiB VRAM.
   - Total idle VRAM: ~5,700–6,000 MiB VRAM.
   - Remaining headroom: ~1,000–1,300 MiB VRAM.
3. **Budget Feasibility for STT/TTS:**
   - FasterWhisper small.en on CUDA (`int8_float16`) consumes ~400 MiB VRAM, safely fitting within the remaining headroom.
   - Kokoro-82M TTS runs entirely on CPU via ONNX Runtime (`0 MiB VRAM`).
   - VLM (Qwen2.5-VL-3B, ~2,200 MiB) CANNOT co-reside with Qwen2.5-7B without exceeding the 8GB ceiling. Strict model swapping or CPU offload is mandatory.
4. **Local-Only Constraint:** Fully verified. No cloud API keys, external telemetry, or remote dependencies are active.

---

## F. Repository Cleanup Report

### 1. Root Files Inventory & Actions Taken

| File (Original Root Path) | New Destination Path | Category | Reason for Move / Retention |
|---|---|---|---|
| `TOM_STATUS_AUDIT.md` | `docs/audits/TOM_STATUS_AUDIT_2026-10-21.md` | Documentation | Historical audit report from 2026-10-21; belongs in docs |
| `pip-freeze-before-pipecat.txt` | `docs/environment/pip-freeze-before-pipecat.txt` | Environment Snapshot | Diagnostic environment freeze; belongs in docs/environment |
| `pipecat-install-plan.json` | `docs/environment/pipecat-install-plan.json` | Environment Snapshot | Pip report artifact from dependency install; belongs in docs/environment |
| `extract_metrics.py` | `scripts/extract_metrics.py` | Developer Tool | Utility to extract latency metrics from tom.log; belongs in scripts |
| `test_cuda_stt.py` | `tests/manual/test_cuda_stt.py` | Manual Test | Direct CUDA FasterWhisper smoke test; belongs in manual tests |
| `test_startup.py` | `tests/manual/test_startup.py` | Manual Test | Direct TomApp config startup check; belongs in manual tests |
| `engine.log` | `logs/engine.log` | Runtime Log | Generated engine log; consolidated into logs/ |
| `engine_stderr.log` | `logs/engine_stderr.log` | Runtime Log | Generated engine log; consolidated into logs/ |
| `engine_stderr2.log` | `logs/engine_stderr2.log` | Runtime Log | Generated engine log; consolidated into logs/ |
| `engine_stdout.log` | `logs/engine_stdout.log` | Runtime Log | Generated engine log; consolidated into logs/ |
| `engine_stdout2.log` | `logs/engine_stdout2.log` | Runtime Log | Generated engine log; consolidated into logs/ |
| `engine_test_stderr.log` | `logs/engine_test_stderr.log` | Runtime Log | Test run stderr; consolidated into logs/ |
| `engine_test_stderr2.log` | `logs/engine_test_stderr2.log` | Runtime Log | Test run stderr; consolidated into logs/ |
| `engine_test_stderr3.log` | `logs/engine_test_stderr3.log` | Runtime Log | Test run stderr; consolidated into logs/ |
| `engine_test_stdout.log` | `logs/engine_test_stdout.log` | Runtime Log | Test run stdout; consolidated into logs/ |
| `engine_test_stdout2.log` | `logs/engine_test_stdout2.log` | Runtime Log | Test run stdout; consolidated into logs/ |
| `engine_test_stdout3.log` | `logs/engine_test_stdout3.log` | Runtime Log | Test run stdout; consolidated into logs/ |
| `full_output.log` | `logs/full_output.log` | Runtime Log | Diagnostic log output; consolidated into logs/ |
| `rust-audio-debug.log` | `logs/rust-audio-debug.log` | Runtime Log | 5.3 MB audio debug log; consolidated into logs/ |
| `stderr.log` | `logs/stderr.log` | Runtime Log | Process stderr; consolidated into logs/ |
| `stdout.log` | `logs/stdout.log` | Runtime Log | Process stdout; consolidated into logs/ |
| `test_output.log` | `logs/test_output.log` | Runtime Log | Test runner output; consolidated into logs/ |
| `voice-debug.log` | `logs/voice-debug.log` | Runtime Log | Voice debug output; consolidated into logs/ |

### 2. Files Deliberately Retained in Root
- `pyproject.toml` — Primary packaging and configuration file.
- `README.md` — Project entry documentation.
- `.gitignore`, `.gitattributes`, `.graphifyignore` — Git and indexing configurations.
- `STATE.md`, `PROGRESS.md`, `HANDOFF.md` — Authoritative project state tracking files.
- `IMPLEMENTATIONPLAN.md` — Active development implementation plan.
- `plan.md` — Master design and architecture reference.
- `STAGE3_DESIGN.md` — Active architecture specification for model residency and swap pipeline.

### 3. Cleanup Candidates Requiring Future Decision
- `tests/docs/perf/00-baseline.md`: Accidental duplicate of `docs/perf/00-baseline.md`. Recommend removing `tests/docs/` once verified.
- `python/tom/tools/check_trace.py` & `selftest_trace.py`: Trace diagnostic utilities placed inside the agent tools package. Recommend moving to `scripts/` or `tools/` to avoid polluting the tool namespace.
- `tests/tools/replay_endpointing.py`: Recommend moving to `scripts/replay_endpointing.py`.

---

## G. Ponytail Audit — Codebase Over-Engineering & Bloat Review

Ranked findings (largest cuts first) for simplifying over-engineering, removing dead code, and relying on standard libraries:

1. `delete:` `python/tom/voice/pipeline.py` (567 lines) legacy monolithic pipeline. Replaced by `interaction_core.py` event loop. Currently duplicates state and callbacks without being used in streaming voice mode. [`python/tom/voice/pipeline.py`]
2. `delete:` Nested duplicate `.venv-ws3-bench` inside benchmark directory (hundreds of MB). Retain only root benchmark environments. [`tests/benchmarks/voice/ws3/.venv-ws3-bench`]
3. `yagni:` `SmartTurnV3Arbiter` complex analyzer initialization when Pipecat is absent. Fallback is pure VAD; 100% false-complete rate renders model unusable. Drop Pipecat extra and use clean VAD hangover until model is retrained. [`python/tom/voice/smart_turn.py`]
4. `shrink:` `python/tom/main.py` monolithic 644-line `TomApp`. Split CLI runner, voice runner, and tray into focused 80-line modules. [`python/tom/main.py`]
5. `stdlib:` Hand-rolled `_delta_ms` nanosecond math across latency tracer. Use `datetime.timedelta` or straightforward float arithmetic. [`python/tom/voice/latency.py`]
6. `shrink:` Redundant `_DefaultArbiterSentinel` pattern in `main.py`. Use standard `None` sentinel with explicit fallback parameter. [`python/tom/main.py:59`]
7. `yagni:` `STAGE3_DESIGN.md` defines multi-model server launching for models not yet downloaded. Retain single-server llama.cpp until multi-model assets are installed. [`STAGE3_DESIGN.md`]

**Net potential savings:** -850 lines of duplicate code, -3 speculative dependencies (`pipecat-ai`, `soxr`, `wait-for2`).

---

## H. Technical Debt & Risk Register

### P0 — Critical (Voice Experience Broken)
- **RISK-01 (Barge-In Gating):** `push_samples` dropping frames when `_gated == True` prevents speech detection during playback. Fix: remove `or self._gated` check from sample ingestion; allow `_process_frame` to evaluate barge-in RMS threshold.
- **RISK-02 (Markdown TTS Leakage):** TTS synthesizer receives raw markdown syntax. Fix: pipe sentence streamer output through `SpeechFormatter.format()`.
- **RISK-03 (STT CUDA Discovery):** CTranslate2 missing DLL path on Windows causes silent fallback to slow CPU STT. Fix: add `os.add_dll_directory` for Python CUDA package directories during initialization.

### P1 — High (Reliability & Metrics)
- **RISK-04 (Whisper Hallucinations):** Autoregressive repetitions on silence pollute agent context. Fix: add regex/repetition filter on committed transcripts before orchestrator dispatch.
- **RISK-05 (Negative Queue Latency):** Inverted stage tuple in `latency.py` corrupts telemetry logs. Fix: update `_METRIC_STAGES["stt_queue_wait_ms"]`.
- **RISK-06 (Smart Turn Incomplete Hang):** Missing timer for `smart_turn_max_incomplete_wait_ms` can leave candidate speech in limbo. Fix: attach scheduled timeout callback on `CONTINUING` decision.

### P2 — Medium (Architecture & Code Health)
- **RISK-07 (Dual Voice Pipelines):** Both `VoicePipelineManager` and `InteractionCore` exist, creating confusion. Fix: deprecate `VoicePipelineManager` in favor of `InteractionCore`.
- **RISK-08 (Test Organization):** Diagnostics placed in `python/tom/tools/` and `tests/docs/`. Fix: move to `scripts/`.

---

## I. Recommended Development Roadmap

### Milestone 1: Voice Pipeline Bugfixes & CUDA Restoration (Est. 1–2 Sessions)
1. Fix barge-in frame gating in `python/tom/voice/microphone.py`.
2. Add `os.add_dll_directory` in `python/tom/voice/stt.py` to enable real CUDA STT on RTX 4060.
3. Pipe streamed sentences through `SpeechFormatter` before TTS synthesis.
4. Correct `_METRIC_STAGES` derivation for `stt_queue_wait_ms`.
5. Add basic Whisper hallucination filter for trailing silence.

### Milestone 2: Voice Latency Calibration & Real Benchmarking (Est. 1 Session)
1. Run real hardware benchmark using `scripts/extract_metrics.py` and `debug_trace.py`.
2. Verify STT decode drops from ~1734 ms (CPU) to < 350 ms (CUDA).
3. Tune clause-boundary first-chunk TTS synthesis to achieve `perceived_response_ms < 1500 ms`.

### Milestone 3: Subsystem Consolidation & Cleanup (Est. 1 Session)
1. Deprecate legacy `VoicePipelineManager` in favor of `InteractionCore`.
2. Move misplaced diagnostic tools from `python/tom/tools/` to `scripts/`.
3. Clean up `tests/docs/` duplicate directory.

### Milestone 4: Multi-Model Residency & Swap Automation (Est. 2 Sessions)
1. Implement automated llama.cpp server management per `STAGE3_DESIGN.md`.
2. Enable on-demand VLM loading for screen inspection tools (`vision.analyze`).

---

## J. Immediate Handoff

The exact task to execute when development resumes is:

**Task:** Fix the 3 critical voice pipeline bugs in `python/tom/voice/`:
1. In `python/tom/voice/microphone.py`: in `push_samples()`, allow audio to reach `_process_frame` when gated so barge-in RMS threshold detection can function during `SPEAKING`.
2. In `python/tom/voice/stt.py`: add Windows CUDA DLL directory discovery via `os.add_dll_directory` pointing to `.venv/Lib/site-packages/nvidia/cublas/bin` and `cudnn/bin`.
3. In `python/tom/voice/interaction_core.py`: wrap sentence streamer output with `SpeechFormatter.format()` before yielding to TTS.
4. In `python/tom/voice/latency.py`: fix `_METRIC_STAGES["stt_queue_wait_ms"] = (LatencyStage.SPEECH_END, LatencyStage.STT_DECODE_START)`.

These fixes are self-contained, targeted, and restore both GPU STT acceleration and voice conversational integrity without altering external contracts.

