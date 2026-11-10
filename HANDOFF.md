# Handoff — Full Repository Audit & Root Cleanup COMPLETE (2026-11-02)

## 2026-11-10 Control Center Runtime Hierarchy Redesign - IMPLEMENTED

Status: code and scoped validation complete; live hardware and process
acceptance remain blocked because starting live TOM services, microphones, and
speakers is prohibited.

- Rust supervisor now exposes `engine`, `model`, `python`, `cli`, and
  `wheel_script` as owned process components. `voice`, `stt`, and `tts` are
  capability records derived from the authenticated Python status body, and
  `vision` is an `unsupported` placeholder with no controls. `start_tom` accepts
  only `python` or `cli`; `brain` and `voice` process specs are rejected. The
  voice-operation whitelist includes `start_pipeline` and `stop_pipeline`.
- Python `TomApp` shares one voice-runtime construction path between `--voice`
  and the management API. `_management_voice_operation` can start/stop the
  pipeline and rejects provider load/unload/tests while a pipeline is active.
  Service-mode lifecycle can report ready/degraded based on the running core and
  microphone instead of always treating Voice as stopped.
- Control Center UI groups supervised processes, voice capabilities, and future
  capabilities. Voice has separate pipeline, Faster-Whisper, and Kokoro cards.
  Vision is clearly marked not implemented and performs no work.
- Docs updated: `desktop/control-center/ARCHITECTURE.md` Alpha 3 section and
  `desktop/control-center/HANDOFF.md`.

Scoped validation: `cargo test --bin tom-control-center` (12 passed); focused
Python voice operations (8 passed); `tests/unit/desktop` (83 passed); Ruff check
on changed Python files; `cargo fmt --check` and
`cargo clippy --all-targets -- -D warnings`; svelte-check 0 errors/0 warnings;
Vite production build; `git diff --check` (CRLF warnings only).

Not verified live: microphone capture, speaker playback, STT/Kokoro loading,
pipeline start/stop against a real Python Runtime, GPU budget under load, and
installer acceptance.

## 2026-11-10 Control Center API, Ownership & Voice Status Stabilization - IMPLEMENTED

Status: code and scoped validation complete; live reproduction remains blocked because
starting live TOM services is prohibited.

Changes:

- Rust supervisor (`desktop/control-center/src-tauri/src/main.rs`): validates
  authenticated `/api/v1/status` readiness (`api_version == 1` and
  `data.python.state`) with a 30s deadline and backoff; reaps exited Python
  children before ownership checks; releases ownership only after confirmed
  child exit; returns owner mode, PID, session, state, and error on conflicts
  instead of stopping the existing session; probes status with a 2500ms timeout;
  caches GPU telemetry for 10s; launches helper processes with
  `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`.
- Python management (`python/tom/main.py`): `_management_status` runs engine,
  model, and audio probes concurrently with 1.5s per-probe bounds; exposes Voice
  `lifecycle_state`, `last_error`, and `captured_samples_scope:
current_capture_session`; reports `pipeline_active` as false when Voice is
  stopped. Debug trace no longer shells out to PowerShell/tasklist; it uses
  `sounddevice`/`psutil`. Helper subprocesses request no console window.
- Control Center UI (`desktop/control-center/src/App.svelte`): uses
  `Promise.allSettled` polling with a persistent poll-error banner; displays the
  current Python owner mode/PID/session and last exit detail; disables conflicting
  Brain/CLI/Voice starts; renders a dedicated Voice lifecycle card
  (STOPPED/STARTING/READY/DEGRADED/FAILED) and clears stale diagnostics when the
  management API is unavailable; labels sample counts as the current capture
  session.

Verification:

- `cargo test --bin tom-control-center` - 10 passed.
- `cargo fmt --check`; `cargo clippy --all-targets -- -D warnings` - clean.
- `python -m pytest tests/unit/desktop -q` - 81 passed; focused voice/control API
  tests - 10 passed.
- `python -m ruff check` on changed Python files - clean.
- `npm run check` - 0 errors, 0 warnings; `npm run build` - success.
- `git diff --check` - no whitespace errors (CRLF warnings only).

Not verified live: authenticated `/api/v1/status` on port 50560, ownership
conflict/reaping against real child processes, and Voice lifecycle behavior with
a physical microphone/speaker.

## 2026-11-07 Command Wheel Stabilization — IN PROGRESS

The earlier Phase 14 wheel report below records preliminary backend checks, not
desktop acceptance. Current stabilization changes and evidence:

- `desktop/tom_wheel.ahk`: Note and Remind now prompt for missing input and ask
  for explicit Yes/No approval; selected-text capture restores the clipboard in
  a `finally` block. OCR is labeled unavailable and exits without dispatch.
  Outside left/right clicks are polled to cancel the overlay.
- `python/tom/desktop/wheel_server.py`: the wheel approval reaches the
  `ToolExecutor` through a context-local `ConfirmationHook` decision and is
  audit logged; empty notes do not call the tool. Remind reports success only
  after a newly created reminder appears in the canonical reminder retrieval
  result. Explain stops if screenshot capture fails.
- `python/tom/schemas/personal.py`: reminder create/update inputs reject past
  timestamps at the tool schema boundary.
- Tests: `tests/unit/desktop/test_wheel_server.py` currently collects 38 tests.
  The focused desktop/personal run passed 96 tests; details below.

| Action    | AHK UI                          | Real tool                                                          | Permission / result                                                                                         | Status       |
| --------- | ------------------------------- | ------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------- | ------------ |
| Ask       | Not interactively exercised     | Mock/unit coverage only                                            | Unverified live                                                                                             | Unverified   |
| Explain   | Not interactively exercised     | Capture failure now stops dispatch                                 | No live screen/tool check                                                                                   | Partial      |
| Search    | Not interactively exercised     | Mock/unit coverage only                                            | No live search check                                                                                        | Unverified   |
| Note      | Confirmation prompt implemented | Canonical tool path retained                                       | Approval/denial and SQLite not live tested                                                                  | Partial      |
| Remind    | Confirmation prompt implemented | Canonical orchestrator/tool path; retrieval checks new persistence | Timestamp validation covered by schema tests; approval, denial, stored UTC and time parsing not live tested | Partial      |
| Status    | Not interactively exercised     | Mock/unit coverage only                                            | No live telemetry check                                                                                     | Unverified   |
| Summarise | Not interactively exercised     | Mock/unit coverage only                                            | No live fetch check                                                                                         | Unverified   |
| OCR       | Marked N/A                      | Not dispatched                                                     | Deferred                                                                                                    | Out of scope |

Verification run:

- `.venv\\Scripts\\python.exe -m pytest -o addopts='' tests/unit/desktop/test_wheel_server.py tests/unit/personal/test_personal_permissions.py tests/unit/personal/test_personal_store.py tests/unit/personal/test_reminder_input_validation.py -q` — **96 passed**, exit 0.
- `.venv\\Scripts\\ruff.exe check` on changed wheel/schema/test Python files — clean; Ruff format check on those files — clean.
- `git diff --check` — clean.
- Installed AutoHotkey v2 started the script and stayed active for the startup parse check; process was stopped afterward. This does not verify wheel clicks or desktop actions.
- No live database mutations were performed. The current temporal prompt supplies local date/time context, but no deterministic natural-language time parser was found; reminder scheduling behavior and timezone/date-boundary accuracy remain unverified.

The wheel's older implementation and preliminary test notes in the following
section are historical and are superseded for acceptance status by this update.

Final architecture: TOM uses llama.cpp at `http://127.0.0.1:65400/v1` through the generic OpenAI-compatible `HttpModelProvider`. Bionic/LM Studio is not an active runtime dependency.

Invariants, hardware, and architecture: **`STATE.md`**. Phase ledger and ADRs 041–062: **`PROGRESS.md`**. Comprehensive Audit Report: **`docs/audits/TOM_COMPREHENSIVE_AUDIT_2026-11-02.md`**.

---

## Latest Fixes (2026-11-06) — Date Awareness & Barge-In

### Fix 1: TOM date/year awareness

**Root cause:** `VOICE_SYSTEM_PROMPT` and `CLI_SYSTEM_PROMPT` contained zero temporal anchoring. Local LLMs default to their 2023 training cutoff when asked about date/time/year.

**Changes:**

- `python/tom/agents/prompts.py`:
  - Added `get_temporal_context(now)` — returns a `CURRENT TEMPORAL CONTEXT:` block with today's date, time, and year.
  - Added `refresh_prompt_temporal_context(prompt, now)` — idempotent: replaces existing block or appends; never duplicates.
  - `build_system_prompt(mode, now=None)` now injects live temporal context on every call.
- `python/tom/agents/orchestrator.py`:
  - Imported `refresh_prompt_temporal_context`.
  - Added `_refresh_temporal_context()` — called at the start of both `run()` and `stream()` to update `messages[0]` (the system prompt) in-place with the current time, keeping every conversation turn anchored to real wall-clock time.
- `python/tom/core/router.py`:
  - Added `"date"`, `"time"`, `"today"`, `"clock"`, `"calendar"`, `"year"` to the `REASONING` keyword list so date/time queries route immediately to the LLM rather than falling through to UNKNOWN.

### Fix 2: Barge-in not working

**Root cause (two parts):**

1. `push_samples()` in `microphone.py` had `if self._stopped or self._muted or self._gated: return` — this dropped 100% of incoming mic frames during TTS gating, so `_process_frame()` and barge-in detection could never run.
2. `_handle_event()` in `interaction_core.py` only called `handle_barge_in()` when `self._active_turn is not None and not self._active_turn.done()`, missing the case where the turn task had already completed but TTS playback (`self._playback_active`) was still running.

**Changes:**

- `python/tom/voice/microphone.py` — `push_samples()`: removed `or self._gated` guard so frames flow into `_process_frame()` during gating. `_process_frame()`: when `_gated`, accumulates RMS into `_barge_in_candidate_frames`; on threshold exceeded for `barge_in_min_duration_ms`, fires `SpeechStartEvent(metadata={"barge_in": True})`, drains pre-roll + candidate frames, and un-gates. Grace window of 3 quiet frames (~60ms) tolerates consonant closures without resetting.
- `python/tom/voice/interaction_core.py` — `_handle_event()`: changed condition to `if (self._active_turn is not None and not self._active_turn.done()) or self._playback_active:` so barge-in fires even when the turn task has completed but audio is still playing.

**Tests added:**

- `tests/unit/voice/test_microphone_loop.py::test_barge_in_detection_while_gated` — verifies frames flow while gated, barge-in fires after sustained loud speech, `SpeechStartEvent` has `{"barge_in": True}`, and candidate audio frames are forwarded.
- `tests/unit/voice/test_identity.py::test_temporal_context_anchoring` — verifies correct date/year/anti-cutoff text in built prompts.
- `tests/unit/voice/test_identity.py::test_refresh_prompt_temporal_context_idempotence` — verifies refresh replaces (not duplicates) existing temporal block.
- `tests/unit/core/test_router.py` — added `"what is today's date"`, `"what time is it"`, `"what year is this"` to reasoning keyword cases.

**Verification:** 311 passed (interaction core + all agent tests); 75 passed (identity + microphone loop + router). Zero regressions.

---

## Latest Implementation (2026-11-06) — Desktop Command Wheel

### Summary

Implemented the 8-segment radial/pie desktop command wheel activated via `Win + Space` (using AutoHotkey v2), backed by a local-only asynchronous HTTP bridge in Python (`127.0.0.1:65401`), routing directly into TOM's canonical `AgentOrchestrator` and `ToolExecutor` pipelines.

### Architecture & Components

1. **AHK v2 Radial Wheel GUI (`desktop/tom_wheel.ahk`)**:
   - Shortcut: `Win + Space` (suppresses native Windows keyboard layout switching).
   - Coordinates: Anchors at cursor position on activation. Smooth multi-monitor clamping prevents off-screen truncation while preserving selection origin.
   - Geometry: 8 equal 45° sectors. Inner dead zone $R=28\text{ px}$, outer radius $R=140\text{ px}$. Visual highlight on hovered segment with hover redraw via GDI+ layered window (`UpdateLayeredWindow`, `WS_EX_LAYERED`).
   - Actions (clockwise from 12 o'clock):
     - **Top**: `Ask` (`?`) → routes selected/free text to `orchestrator.run()`.
     - **Upper-Right**: `OCR` (`O`) → transparent drag rectangle overlay for region select → `vision.ocr` → copies extracted text to clipboard.
     - **Right**: `Explain` (`E`) → `vision.capture` active window title → asks reasoning orchestrator for concise analysis.
     - **Lower-Right**: `Search` (`S`) → `web.search` + `web.fetch` via orchestrator with selected text.
     - **Bottom**: `Note` (`N`) → `personal.note` tool saving selected text with `#wheel` tag.
     - **Lower-Left**: `Remind` (`R`) → `personal.reminder` via orchestrator scheduling pipeline.
     - **Left**: `Status` (`#`) → queries `system.get_snapshot` and `system.list_processes` → temporary tray tip notification auto-dismissed in 3s.
     - **Upper-Left**: `Summarise` (`~`) → clipboard URL check / `InputBox` fallback → `web.fetch` + reasoning via orchestrator.
   - Cancellation: `Escape`, right-click (`WM_RBUTTONDOWN`), or left-click outside dead zone/outer radius safely closes without executing.
   - Context Acquisition: Selected text grabbed safely via `Ctrl+C` with full clipboard backup (`ClipboardAll`) and restoration; non-destructive.
   - IPC Dispatch: Dispatches asynchronous HTTP POST to `127.0.0.1:65401/wheel` using `WinHttp.WinHttpRequest.5.1` in a timer thread to never block the AHK input loop.

2. **HTTP Bridge Server (`python/tom/desktop/wheel_server.py`)**:
   - Host: `127.0.0.1` (loopback only, never bound externally).
   - Port: `65401`.
   - Endpoints:
     - `POST /wheel`: validates JSON schema `{action, context, region, request_id}`, dispatches to `AgentOrchestrator` or `ToolExecutor` (enforcing full `PermissionEngine` and `ConfirmationHook` checks), returns `{"ok": bool, "result": str, "request_id": str, "error": str|null}`.
     - `GET /health`: lightweight health check endpoint `{"ok": true}`.
   - Lifecycle: Supervised task in `TomApp._run_cli()` and `TomApp._run_voice()` in `python/tom/main.py`. Cancels cleanly with app shutdown.

3. **Validation & Tests (`tests/unit/desktop/test_wheel_server.py`)**:
   - 36 unit tests verifying: dispatch for all 8 actions, fallback prompt when context is empty, region passing for OCR, note saving, system snapshot & top process gathering, unknown action 400 rejection, invalid JSON 400 rejection, request ID echo, Pydantic model validation, coordinate boundary validation, concurrency bounding via semaphore, in-flight duplicate rejection (409), duplicate response caching (TTL), and `build_app` routing.
   - Suite results: 69 passed across `tests/unit/desktop/` (33 tray + 36 wheel server).
   - Live End-to-End Acceptance:
     - `status`: Verified with real system metrics (`CPU 4.35% RAM 92.9% GPU 0.0%`, top process `Antigravity IDE.exe`).
     - `ask`: Verified with real local `Qwen2.5-7B-Instruct` model (`"What is 2 + 2?"` -> `"4"`).
     - `search`: Verified with real DuckDuckGo web search + fetch + model summarization (`"DuckDuckGo is a search engine..."`).
     - `summarise`: Verified with real SSRF-safe `web.fetch` on `https://example.com` + model summarization.
     - `remind`: Verified with real reminder scheduling (`"buy milk tomorrow at 10am"` -> `"Sure, I've set a reminder to buy milk..."`).
     - `explain`: Verified with active window detection + reasoning analysis.
     - `note`: Verified permission policy enforcement: correctly rejected unconfirmed mutation with 403 (`User confirmation denied for tool 'personal.note': Tool 'personal.note' requires explicit user confirmation prior to execution`).
     - `ocr`: Screen capture BitBlt requires an active interactive display session; validated through unit test vectors with bounding box coordinates.

---

## Resume

- **Done:** Phase 9 Iterations **9.0–9.4** (VAD/endpointing, streaming STT, LLM stream/sentences, streaming TTS/playback, turn manager/response policy) and applicable 9.B checkpoint work.
- **Done:** Phase 10 **Iterations 1–7** (34 canonical tools registered, schema export, data-driven permissions, SSRF-safe `web.search`/`web.fetch`, SQLite personal tools, ephemeral `vision.analyze`, exit gate verified).
- **Done:** Phase 11 **Iterations 11.1–11.6** (Secret scrubbing via regex catalog + Shannon entropy heuristic, centralized `PathPolicy` jail & sensitive file blacklist, stdin-safe confirmation hook & anti-flooding rate limits, structured `SecurityAuditLogger`, `ToolExecutor` input/output/error scrubbing with typed `BaseModel` preservation, 101 concrete adversarial tests, and secret scanner benchmark).
- **Done:** Phase 12 **Iterations 12.1–12.5 CLOSED & COMPLETE** (Atomic Resource Governor, Engine Telemetry Cache, Model Lifecycle Coordinator, Priority Resource Queue Arbitration & Cooperative Preemption, Adversarial Concurrency Stress Suite with 100 cycles, and Focused Validation Gate).
- **Done:** Phase 13 **IPC Stabilization, Tool Grounding & Runtime Fixes** (IPC idle/reconnect reliability, engine health accuracy, tool grounding/anti-fabrication, startup constructor fix).
- **Done:** Phase 14 **Tray Mode & Desktop Integration** (System tray daemon with pystray, engine connection status monitoring, mute toggle, CLI/voice mode support).
- **Done:** **Voice Pipeline Robustness Fix (2026-10-23)** — CUDA fallback memoization, STT error recovery, IPC disconnect handling, Ctrl+C responsiveness, timeouts on blocking ops.
- **Done:** **Rust Engine Hardening & Runtime Validation (2026-11-06)**:
  - R0 cuts: Deduplicated `dispatch_with_timeout` into `dispatch()` delegation; documented `chunk_size` for mock tests; simplified baseline handler construction; folded `AudioManager` playback state into a single `PlaybackState` mutex.
  - R1 IPC & Audio reliability: Guaranteed complete response writes without mid-frame cancellation; bounded writes with 2000ms deadline; added mid-frame `underrun_count` to audio playback; added ring buffer `overrun_count` to audio capture; surfaced both counters in `AudioStatus`; optimized `SystemMonitor::get_all()` to query GPU/battery/disk lock-free and acquire `sysinfo` once; offloaded CPAL audio stream creation to `spawn_blocking` to prevent Tokio worker stalls.
  - R2 Activation: Wired `EventBus` to an engine log-sink task; initialized `HotkeyManager` with default `toggle_listening` (Ctrl+Space); spawned OS listener `run_os_listener` in `main.rs`; added `input.register_hotkey`, `input.unregister_hotkey`, and `input.list_hotkeys` (wrapping in `{bindings, total}` dictionary payload for Python schema compliance).
  - Validation: 9-task validation audit completed (see `validation_report.md`). Live smoke tests against compiled release binary passed 20 of 21 checks (1 documented FAIL: Ctrl+Space server-push to Python voice pipeline deferred to protocol v2). 102 passed Rust tests (95 unit + 7 integration); 174 passed Python IPC tests (161 unit + 13 integration); release and debug binaries built fresh; Cargo clippy, Cargo fmt, and Ruff clean.
- **Done:** **Full Repository Audit & Root Directory Cleanup (2026-11-02)** — Comprehensive audit across 15 subsystems, root files consolidated into `logs/`, `docs/audits/`, `docs/environment/`, `tests/manual/`, and `scripts/`. Exact voice pipeline defect targets isolated and documented in `docs/audits/TOM_COMPREHENSIVE_AUDIT_2026-11-02.md`.
- **Done:** **Phase V1 Voice Repair (Items 0–6) (2026-11-02)**:
  - Item 0 (`bac0a41`): Hermetic test baseline recorded (1760 passed Python, 100 passed Rust).
  - Item 1 (`b440392`): `--bench-voice <wav>` CLI replay mode implemented, baseline measurements saved to `docs/perf/bench_voice_baseline_1793627372.json`.
  - Item 2 (`41d3d5e`): STT on GPU via Windows nvidia DLL directory registration, warm-up decode, VRAM residency registration. Measured: STT decode median drops from ~5s to 110ms (RTF=0.030), VRAM delta=452 MiB.
  - Item 3 (`e3ae93a`): `SpeechFormatter.format()` integrated before TTS synthesis in `interaction_core.py` to strip markdown bold, bullets, headers, backticks, URLs, and numbered lists.
  - Item 4 (`14ad8a6`): STT hallucination filter using segment stats (`no_speech_prob`, `avg_logprob`, `compression_ratio`), secondary phrase blocklist, `condition_on_previous_text=False`, and dropped counter.
  - Item 5 (`7c798c7`): Smart Turn force-complete timeout on `CONTINUING` decision scheduled via `smart_turn_max_incomplete_wait_ms`, cancelled on new speech.
  - Item 6 (`c014fc2`): Telemetry honesty fixes: `stt_queue_wait_ms` spans `SPEECH_END -> STT_DECODE_START`; identical marks report `not_measured` (never 0 = pass); `playback_completed` tracks elapsed playback time against engine buffer drain.
  - Item 7: DESIGN ONLY (transitioned to Phase V2 Step 5 with approved design).
  - Current verification exit: 1771 passed Python unit tests (`pytest tests/unit -q --timeout=30`), 93 passed Rust engine tests.
- **Done:** **Phase V2 Step 0 (Reconcile & Audit Alignment) (2026-11-02)**:
  - Reconciled `docs/audits/TOM_COMPREHENSIVE_AUDIT_2026-11-02.md` against live code: marked CUDA STT, markdown stripping, hallucination filter, queue wait latency, and Smart Turn force-complete as RESOLVED with file:line references.
  - Cleaned redundant `tests/docs/perf/00-baseline.md`.
  - Next: Step 1 (Live Baseline).
- **Done:** **Voice UX Phase (Wake Word, Identity, Greeting & Session Lifecycle) (2026-11-04)**:
  - Silent startup: `startup_greeting=False` by default; TOM boots completely quiet and dormant until explicitly awakened.
  - Robust wake-word gating: `python/tom/voice/wakeword.py` matches "Hey TOM", "TOM", "Hi TOM", "OK TOM" with full punctuation tolerance ("Hey, Tom.", "Tom.", "Tom,") and Whisper acoustic homophones ("Hey Dom", "Don?"), preventing Whisper transcription dropouts while rejecting unrelated speech. FasterWhisper STT seeded with `initial_prompt="Hey TOM. TOM."` to bias language model decoding toward "TOM".
  - Dynamic greeting on wake-up: Standalone wake-up on first conversation or after 30s timeout break greets with natural, time-aware greeting ("Good evening. I'm listening.", etc.); embedded commands ("Hey TOM, what time is it?") wake and execute immediately without double ack.
  - Inactivity sleep: Inactivity countdown (30s) puts TOM into dormant sleep; wake phrase reliably re-awakens TOM every time.
  - Verification: 59 voice unit tests passed (14 wake-word, 5 greetings, 40 interaction core). Ruff clean; `git diff --check` clean.
- **Runtime:** Generic HTTP provider with llama.cpp as the production endpoint; no backend-specific alias remains.
- **Phase 12 Closeout Validation:**
  - Focused test suite: **488 passed, 8 skipped** (uninstalled optional deps `opencv-python`/`winocr`), zero failed across `tests/unit/resources/`, `tests/unit/agents/`, `tests/unit/vision/`, `tests/integration/tools/test_tool_pipeline.py`.
  - Resource tests alone: **105 passed** (19 stress + 16 governor integration + 15 model manager + 12 priority integration + 14 resource manager + 24 policy + 5 telemetry).
  - 100 repeated adversarial contention cycles executed with verified zero accounting drift, zero leaked leases, and zero abandoned waiters.
  - Quality checks: Ruff check clean, Ruff format clean (30 files checked), `git diff --check` clean.
- **Phase 13 Targeted Validation:**
  - IPC tests: 68 passed (including 6 new reconnection cycle tests)
  - Core tests: 55 passed
  - Tools tests: 230 passed (including 8 new fabricated-result prevention tests)
  - Agents tests: 264 passed
  - Integration tests: 87 passed
  - Voice tests: 351 passed, 2 skipped
  - Resources tests: 107 passed
  - Security tests: 344 passed
  - Quality checks: Ruff check clean, Ruff format clean, `git diff --check` clean
- **Phase 14 Tray Mode Validation:**
  - Tray unit tests: 33 passed (`tests/unit/desktop/test_tray.py`)
  - Total targeted tests: 1620+ passed across all modules
  - Tray daemon verified for CLI/voice modes with engine status monitoring
  - Mute toggle, quit action, connection status display verified
  - Quality checks: Ruff check clean, Ruff format clean, `git diff --check` clean
- **Subsystem Architecture Summary:**
  - **Resource Admission & Headroom (ADR 054, ADR 060):** `ResourceManager` enforces software reservation limits (`max_vram_mb=7200`, `max_ram_mb=16384`) with a strict 500 MB safety headroom margin. Mutual exclusion is enforced between heavy roles (`ModelRole.REASONING` ~5.2 GB and `ModelRole.VLM` ~5.5 GB).
  - **Telemetry Snapshot Caching:** `EngineTelemetryProvider` pro-actively refreshes hardware telemetry via Rust engine IPC with a 250 ms snapshot cache and graceful fallback.
  - **Lease Lifecycle & Scope (ADR 059):** Reasoning leases in `AgentOrchestrator` are scoped per generation step and released before tool dispatch, eliminating nested deadlocks when tools invoke `VisionManager.analyze()`.
  - **In-Flight Inference Safety (ADR 060):** Streaming leases remain held until client `.aclose()` finishes, preventing premature re-grant while server inference is still active.
  - **Model Lifecycle Bookkeeping (ADR 061):** `ModelLifecycleCoordinator` maintains an explicit state machine (`UNLOADED`, `READY`, `BUSY`, `IDLE`, `ERROR`) and executes pluggable idle-eviction callback hooks via injected clock. State tracking is strictly software bookkeeping.
  - **Priority Arbitration & Fairness (ADR 062):** `PriorityResourceQueue` arbitrates contended capacity by `TaskPriority` (0: VOICE_INTERACTIVE, 1: USER_INTERACTIVE, 2: ROUTING, 3: REASONING, 4: VISION, 5: BACKGROUND). Starving background waiters are promoted by 1 level every 15 seconds. Equal priority is admitted FIFO. `TaskPriority.BACKGROUND` propagates through `ProactiveScheduler` → `TaskExecutor` → `ToolExecutor` → `VisionManager` → `ResourceManager.acquire()`.
  - **Cooperative Preemption:** Proactive tasks poll `CancellationToken` checkpoints. Preemption is an advisory yield signal, not forced termination.
  - **Grant/Reservation Boundary Protection:** Cancellation or timeout at the exact boundary of grant or heavy lock acquisition cleanly rolls back the reservation, frees the lease, and dispatches the next queued waiter.
- **Architectural Limitations:**
  - Software reservations are NOT a guaranteed physical VRAM ceiling. External processes or internal llama.cpp fragmentation cannot be directly observed.
  - Client-side stream closure (`aclose()`) signals connection termination but is not physical proof of server-side CUDA memory deallocation.
  - Logical model eviction is bookkeeping, not proof of physical engine unload.
- **Next Phase:** Phase 13 — Agent Evaluation, Benchmarks & Hardening. (Do not start Phase 13 until authorized by an explicit user prompt). Do NOT redesign or rebuild Phase 12 components.

## Pipeline assembled so far (9.0 → 9.4)

```
audio → MicrophoneLoop(9.0) → SpeechStart/Frame/End → StreamingSTTProvider(9.1)
      → ConversationTurnManager + ResponsePolicy(9.4)
      → AgentOrchestrator.stream()(9.2) → SentenceStreamer(9.2)
      → StreamingTTSProvider(9.3) → EngineClient.play_audio_buffer() / stop_audio_playback()
```

Each stage is verified **independently**. 9.5 is the first iteration to bind them into one live event loop; do not move logic back down into 9.5.

| Piece                  | Path                                                                                                                                                                                                                                                 |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Events / VAD / PCM     | `python/tom/voice/events.py`, `vad.py`, `audio_utils.py`, `microphone.py`                                                                                                                                                                            |
| STT                    | `python/tom/voice/streaming_stt.py` (`StreamingSTTProvider`, `FasterWhisperStreamingAdapter`, `PartialTranscript`)                                                                                                                                   |
| LLM stream / sentences | `python/tom/agents/orchestrator.py` (`stream()`), `python/tom/voice/sentence_streamer.py`                                                                                                                                                            |
| TTS + playback         | `python/tom/voice/streaming_tts.py` (`StreamingTTSProvider`, `IncrementalTTSStreamer`, `SynthesisChunk`)                                                                                                                                             |
| Turn state / policy    | `python/tom/schemas/turn.py`, `python/tom/voice/turn_manager.py`, `python/tom/voice/response_policy.py`                                                                                                                                              |
| Exports                | `python/tom/voice/__init__.py`                                                                                                                                                                                                                       |
| Tests                  | `tests/unit/voice/test_microphone_loop.py` (17), `test_streaming_stt.py`, `tests/unit/agents/test_orchestrator_streaming.py`, `test_sentence_streamer.py`, `test_streaming_tts.py` (18), `test_turn_manager.py` (29), `test_response_policy.py` (32) |
| WS0/WS1 Benchmarks     | `tests/benchmarks/voice/benchmark_stt_tuning.py`, `tests/benchmarks/voice/results/stt_tuning_results.json`                                                                                                                                           |
| WS0 Hardware Test      | `tests/integration/voice/test_hardware_latencies.py::test_real_stt_first_partial_meets_sla`                                                                                                                                                          |

## Phase 10 Tools (Iterations 1–5)

| Tool                | Path                           | Description                                                                                   |
| ------------------- | ------------------------------ | --------------------------------------------------------------------------------------------- |
| `web.search`        | `python/tom/tools/web.py`      | Local SearXNG search; bounded results; SAFE                                                   |
| `web.fetch`         | `python/tom/tools/web.py`      | SSRF-safe fetch; redirect validation; HTML extraction; SAFE                                   |
| `personal.note`     | `python/tom/tools/personal.py` | Note CRUD; SQLite-backed; retrieve SAFE / mutations ASK_USER                                  |
| `personal.calendar` | `python/tom/tools/personal.py` | Calendar event CRUD; UTC timestamps; retrieve SAFE / mutations ASK_USER                       |
| `personal.reminder` | `python/tom/tools/personal.py` | Reminder CRUD + recurrence + ProactiveScheduler lifecycle; retrieve SAFE / mutations ASK_USER |
| `vision.analyze`    | `python/tom/tools/vision.py`   | Ephemeral screen/crop analysis via VisionManager (OCR, CV, VLM); SAFE                         |

| Tests                     | Path                                                      |
| ------------------------- | --------------------------------------------------------- |
| Unit web search           | `tests/unit/tools/test_web_search.py` (8)                 |
| Unit web fetch            | `tests/unit/tools/test_web_fetch.py` (27)                 |
| Integration tools         | `tests/integration/tools/test_tool_pipeline.py` (223)     |
| Personal store            | `tests/unit/personal/test_personal_store.py` (39)         |
| Personal reminder service | `tests/unit/personal/test_reminder_service.py` (17 async) |
| Personal permissions      | `tests/unit/personal/test_personal_permissions.py` (15)   |
| Vision tools              | `tests/unit/tools/test_vision_tools.py` (26)              |

### 10.1 `web.search` contract

- Input: `SearchRequest(query: str, max_results: int=10)` — query 1-256 chars, results 1-100
- Output: `SearchResponse(query, count, results[SearchResult], provider)` — results have title, url, summary, source
- Provider: `SearXNGSearchProvider` — configurable endpoint, timeout, max response bytes (1 MiB)
- Permission: SAFE (read-only metadata)

### 10.2 `web.fetch` contract

- Input: `FetchRequest(url, max_response_bytes=1MiB, timeout=10s, max_redirects=10, allowed_content_types=[text/html, text/plain, ...])`
- Output: `FetchResponse(url, final_url, status_code, content_type, content_length, extracted_text, title, redirect_count, untrusted=true)`
- Provider: `BuiltinFetchProvider` — httpx-based with manual redirect handling
- SSRF protection: Public host validation (rejects loopback, private, link-local, multicast, reserved, IPv4-mapped IPv6); host revalidation on every redirect hop (including same-host redirects); scheme validation (HTTP/HTTPS only); single-resolution IP-pinning URL rewrite with multi-address fallback on connect-level errors only (never HTTP-status errors, never unvalidated addresses); original `Host` header with port omitted for default ports (443/80), bracketed for IPv6; `sni_hostname` request extension (`httpcore/_async/connection.py:107, 151` [EVIDENCE: verified, httpcore 1.0.9]) on every hop; `trust_env=False` (prevents proxy DNS interception). `final_url` always shows original hostname, not pinned IP. Remaining limitation: depends on httpcore honoring `sni_hostname` (verified); system proxies ignored via `trust_env=False`.
- Content extraction: BeautifulSoup for HTML (removes script/style/iframe/embed/object); plain text passthrough; 1 MiB output limit
- Security: Fetched content marked `untrusted: true`; cannot influence permission decisions
- Permission: SAFE (read-only, no execution)

## 9.3 contract for 9.5

```
AsyncGenerator[str]  (SentenceStreamer)
   → StreamingTTSProvider.stream_synthesize(...)  → AsyncGenerator[SynthesisChunk]   (ordered)
   → IncrementalTTSStreamer                      → EngineClient.play_audio_buffer() (sequential)
                                                    EngineClient.stop_audio_playback() (once, on cancel)
```

- `SynthesisChunk` is a **frozen** dataclass: `sentence`, `audio_data`, `chunk_index`, `is_last`.
- Synthesis runs ahead of playback (bounded pool + semaphore); playback is a **separate task**, so order is guaranteed by the sequencer, not by synthesis timing.
- On cancellation: pending synthesis is **discarded immediately**, in-flight synthesis is **detached** (never awaited), and `stop_audio_playback()` is invoked **exactly once**.
- Still Decision **041** utterance-buffered playback — no streaming binary IPC.

## 9.4 contract for 9.5

- `ConversationTurnManager.start_turn(...)` → `TurnContext`; close with `record_assistant_reply(...)` or `interrupt_turn(...)`.
- Referents (`last_file`, `last_directory`, `last_query`, `last_screen`, `last_tool`) are created by **deterministic tool→slot rules** and persist across turns. Resolution is the model's job via `TurnContext.to_model_context()` / `render_model_context()`.
- `ResponsePolicy.decide(user_intent, turn_context) -> ResponseDecision` (fixed precedence: `empty → silence → progress → ambiguity → destructive-without-target → latency-bearing → direct`). Emit `ack_text` immediately when action is `FAST_ACK_THEN_WORK`; do nothing when `SILENT_ACT`.
- `record_tool_timing(tool_name, duration_ms)` feeds the latency heuristics; 9.5 should record real durations.
- **No** coreference/NLP engine was added; do not add one in 9.5 either.

## Adaptations to keep

- 9.0: energy VAD in **Python**, not WebRTC/Rust. Ingest **polls** `audio.get_speech`. `mute()` / `pause()` are drain-and-discard and do **not** call `stop_audio_capture`.
- 9.3: pipelining implemented in **Python** on top of the existing buffered playback IPC, not via a new Rust streaming path.
- 9.4: latency classification is **operation type + tool metadata + recorded history**, never a hard-coded per-tool duration.
- WS1: `beam_size=1` is the only flag that helps (~10-15% faster). The 30 s window padding in faster-whisper is the fundamental bottleneck.

## Known gaps (account for in later iterations)

- Offline tests ≠ real mic / TTS latency (**9.6**).
- `VoicePipelineManager` is still untouched; 9.5 must decide how `InteractionCore` relates to it (Decision **044** says do not duplicate its state machine).
- Epoch invalidation and turn recovery are still **unimplemented** — 9.5 owns them.
- Phase 9.0–9.4 tests are **not** in the last full-repo 1301 baseline.
- **STT tradeoffs observed (Checkpoint 1 Fix Round)**:
  - `faster-whisper small.en (beam_size=1)`: Best accuracy overall (WER 13.02%, commands 7.22%, numbers exact 5/8, numbers WER 9.82%) [MEASURED: `tests/benchmarks/voice/ws2/results/rescored_accuracy.json`]. CPU scales cleanly with zero spin-waiting (406% of 1 core / 20.3% of machine at 4 threads) [MEASURED: `f5_cpu_rebuild.json`]. Latency is p50=1570.9 ms, p95=2195.2 ms from endpoint due to 30 s window padding.
  - `Moonshine-base int8 offline`: Best latency-accuracy tradeoff among fast offline models (p50=70.42 ms, p95=99.99 ms in 2–3 s bucket, peak RAM 349.1 MB, zero high-variance flags) [MEASURED: `f4_candidate_models.json`]. Commands WER is 13.89%, but overall WER is 26.55% (questions 35.05%, names 34.90%). Exhibits ONNX Runtime spin-waiting at threads>=2 (1298% of 1 core / 64.9% of machine at 4 threads).
  - `Moonshine-tiny int8 offline`: p50=33.93 ms, p95=57.17 ms, but high variance (31.7%) and overall WER is 40.92% (commands 35.22%).
  - `sherpa-large streaming`: Meets latency (p50=28.98 ms with real H=600 ms) [MEASURED: `f6_h600_latency.json`], but REJECTED FOR ACCURACY (WER 55.40%, commands 55.78%, numbers exact 0/8) due to LibriSpeech training domain mismatch.
  - `NeMo Parakeet TDT`: 110M model is NOT FOUND on HF (empty repo); 0.6B int8 model is NOT SUPPORTED in installed sherpa-onnx 1.13.8 (crashes with `InitDecoder:303 'vocab_size' does not exist in metadata`).
  - `Real VAD hangover`: `H = 600 ms` in TOM config [SOURCE: `python/tom/voice/microphone.py` L48].
  - `Smart Turn V3 Stabilization`:
    - **Dependency isolation**: `smart-turn` declared as optional extra in `pyproject.toml` (`pipecat-ai>=1.12.0`, `loguru>=0.7.0`, `soxr>=0.5.0`). Core voice engine runs cleanly without Pipecat via pure VAD endpointing.
    - **Safe initialization**: `SmartTurnV3Arbiter` lazily loads and checks availability via `check_is_available()`. Missing dependencies raise `SmartTurnUnavailableError` with clear diagnostic context rather than crashing.
    - **Deterministic VAD fallback**: If turn arbitration fails or is uninstalled, `InteractionCore` falls back to VAD speech end (`H=600ms`), preserving candidate audio, bumping epoch exactly once, and avoiding double-commits or leaked tasks.
    - **Benchmark measurements (WS3 re-test on 4 threads, CPUExecutionProvider, smart-turn-v3.2-cpu.onnx)**:
      - Complete turns: 40/41 correct (97.6%), p50=50.3ms, p95=54.0ms, false-incomplete=1, false-complete=0.
      - Hesitation prefixes (15 WS3 clips): 0/15 correct (0.0%), p50=47.5ms, p95=52.2ms, false-complete=15 (100% false-complete).
      - Hesitation full turns: 14/15 correct (93.3%), p50=46.0ms, p95=51.9ms, false-incomplete=1, false-complete=0.
      - Synthetic 60% truncated: 6/41 correct (14.6%), p50=48.1ms, p95=52.7ms, false-complete=35.
      - **Conclusion**: Smart Turn V3 CANNOT be used as sole endpointer due to 100% false-complete rate on hesitation pauses; VAD hangover is mandatory and authoritative. Status remains **UNDER REVISION**.

## Verification & Reproduction Commands (Checkpoint 1 Fix Round)

```powershell
# F1: Package versions audit
.venv-ws2-bench\Scripts\python.exe tests\benchmarks\voice\ws2\audit_versions.py

# F2: Normalizer unit tests & rescoring across all engines
.venv-ws2-bench\Scripts\python.exe tests\benchmarks\voice\ws2\normalizer.py
.venv-ws2-bench\Scripts\python.exe tests\benchmarks\voice\ws2\rescore_all.py

# F3: Clipped-start silence padding diagnostic (A/B)
.venv-ws2-bench\Scripts\python.exe tests\benchmarks\voice\ws2\f3_clipped_diagnostic.py

# F4: Candidate models evaluation (Moonshine-base & Parakeet)
.venv-ws2-bench\Scripts\python.exe tests\benchmarks\voice\ws2\eval_candidates_f4.py
.venv-ws2-bench\Scripts\python.exe tests\benchmarks\voice\ws2\test_parakeet.py

# F5: CPU measurement rebuild (continuous 100ms sampling)
.venv-ws2-bench\Scripts\python.exe tests\benchmarks\voice\ws2\f5_cpu_rebuild.py

# F6: Latency recomputation with real H=600ms
.venv-ws2-bench\Scripts\python.exe tests\benchmarks\voice\ws2\measure_h600.py
```

## Phase 10.3+4 Personal Tools contracts

### 10.3 `personal.note` contract

- Input: `PersonalNoteInput(action: create|retrieve|update|delete, note_id?, title?, content?, tags?, query?, limit=20)`
- Output: `PersonalNoteOutput(operation, note?, notes[], count, deleted)`
- Provider: `PersonalStore` — `personal_notes` SQLite table; stable `note_*` IDs; UTC timestamps
- Permission: retrieve=SAFE; create/update/delete=ASK_USER (escalated by `PERSONAL_ACTION_ESCALATION`)

### 10.3 `personal.calendar` contract

- Input: `PersonalCalendarInput(action: create|retrieve|update|delete, event_id?, title?, description?, start_utc?, end_utc?, window_start_utc?, window_end_utc?, limit=20)`
- Output: `PersonalCalendarOutput(operation, event?, events[], count, deleted)`
- Provider: `PersonalStore` — `personal_calendar_events` table; `evt_*` IDs; explicit UTC; end must be > start
- Permission: retrieve=SAFE; create/update/delete=ASK_USER

### 10.4 `personal.reminder` contract

- Input: `PersonalReminderInput(action: create|retrieve|update|cancel|delete, reminder_id?, message?, fire_utc?, recurrence?, max_fires?, limit=20)`
- Output: `PersonalReminderOutput(operation, reminder?, reminders[], count, deleted)`
- Provider: `PersonalStore` + `ReminderService` — `personal_reminders` table; `rem_*` IDs; recurrence: one_shot|daily|weekly|monthly; bounded max_fires
- Permission: retrieve=SAFE; create/update/cancel/delete=ASK_USER
- Scheduler: `ReminderService.tick()` drives due reminders through existing `ProactiveScheduler.dispatch_proactive_task()` (quiet hours, preemption, rate limit, duplicate suppression via optimistic `fires` guard)
- No arbitrary plan execution: `plan=None` on all reminder-dispatched tasks

## Phase 10.5 `vision.analyze` contract

- Input: `VisionAnalyzeInput(query: str, monitor?: int, region?: [left, top, right, bottom], capability?: ocr_only|element_location|fast_vlm|primary_vlm|deep_vlm, max_tokens: int=512)`
- Output: `VisionAnalyzeOutput(text: str, capability_used: str, bounding_boxes: list[BoundingBox], elements_count: int, execution_time_ms: float)`
- Provider: `VisionManager.analyze` — capability-driven routing (OCR -> CV -> VLM); checks VRAM headroom before VLM; zero disk persistence (ephemeral in-memory PIL/CapturedFrame processing only)
- Privacy: Foreground window evaluated against `PrivacyShield`; blocked on sensitive content
- Permission: SAFE (read-only perception analysis)

## Phase 11 Security Foundation

### 11.1 Secret Scrubbing (`python/tom/security/secrets.py`)

- `SecretScrubber`: Regex catalog (OpenAI `sk-` / `sk-proj-`, Anthropic `sk-ant-`, GitHub PAT, AWS, PEM keys, JWT/Bearer tokens) + Shannon entropy ($H \ge 4.5$ bits/char for tokens $\ge 24$ chars with $\ge 3$ character classes: uppercase, lowercase, digits, symbols).
- Whitelist exemptions: UUIDs, SHA-256 hex, ISO timestamps, data URL prefixes.
- Fail-safe fallback: Scanner errors redact to `***REDACTION_ERROR***`.
- Integrated across `telemetry/logging.py`, `ToolExecutor.execute()`, and confirmation prompts.

### 11.2 Path Policy & Filesystem Jail (`python/tom/security/policies.py`)

- `PathPolicy`: Centralized path confinement delegated to by `PathGuard` in `tools/files.py`.
- Lexical rejections: `..`, Alternate Data Streams (`:stream`), 8.3 short-names (`~`), device namespaces (`\\.\`, `\\?\`), cross-drive relative paths (`D:foo`).
- Boundary containment: Canonical resolution (`Path.resolve()` + `os.path.realpath()`) enforced with `path.is_relative_to(base)`. Parent directory boundary check for non-existent write targets. Symlink/junction breakout rejected.
- Sensitive file blacklist: `.env`, `id_rsa`, `id_ed25519`, `credentials`, etc., blocked with `SecurityError`.

### 11.3 Interactive Confirmation Hardening (`python/tom/security/confirmation.py`)

- `ConsoleConfirmationHook`: Checks `sys.stdin.isatty()`. In non-interactive/headless environments, returns `False` immediately without blocking.
- Anti-flooding rate limit (`ConfirmationTracker`): Maximum 5 prompts per sliding 30-second window; 10-second cooldown auto-denial on flood.
- Parameter sanitization: Previews scrubbed with `SecretScrubber.scrub_object()`.
- Append-only audit trail (`python/tom/security/audit.py:SecurityAuditLogger`): Structured logging to `tom.security.audit`. Fails safe to `SecurityError` denial if audit record cannot be written.

### 11.4 Tool Result Scrubbing & Model Preservation (`python/tom/tools/executor.py`)

- `ToolExecutor.execute()`: Intercepts raw params (scrubbed before confirmation), validation errors (scrubbed), and tool outputs/errors across all execution branches.
- Typed `BaseModel` preservation: Revalidates scrubbed dict into original Pydantic model (`_safe_scrub_data`) so callers receiving `ToolResult(data=FetchResponse(...))` retain typed attributes like `.untrusted`.
- Task cancellation: Re-raises `asyncio.CancelledError` without swallowing.

## What NOT to do

- Do NOT edit files under `config/`, `rust/`, or touch IPC protocol without explicit scope.
- Do NOT weaken Phase 11 security invariants: do not bypass `PathPolicy` or `SecretScrubber`.
- Do NOT modify production `.venv` (use throwaway `.venv-ws2-bench` or `.venv-ws3-bench` for voice benchmarks).
- Do NOT start WS4–WS7 or revise `docs/design/turn_taking_controller.md` before user sign-off.
- Do NOT use GPU STT (CPU-first invariant).
- Do NOT trust unconsumed generator timings or unpaced streaming audio feeds.

## Smart Turn V3 — Dependency Strategy & Status

**Smart Turn is an optional, advisory-only component. TOM operates fully without it.**

### Core runtime (default install)

```powershell
pip install -e .
```

No Pipecat, no loguru, no soxr. TOM uses pure VAD endpointing (600 ms hangover). `SmartTurnV3Arbiter.is_available` returns `False`. `InteractionCore` and `main.py` automatically fall back to VAD-only mode and log `smart_turn_unavailable_using_vad_endpointing`.

### Smart Turn optional install

```powershell
pip install -e ".[smart-turn]"
```

Installs `pipecat-ai>=1.12.0`, `loguru>=0.7.0`, `soxr>=0.5.0`, and `wait-for2>=0.1.0` (Python < 3.12 only).

**Dependency consequences:**

Installing `[smart-turn]` will downgrade `onnxruntime` from 1.30.0 to 1.24.4 and `protobuf` from 7.36.2 to 6.33.6. These downgrades are verified compatible with `faster-whisper 1.2.1` and `kokoro-onnx 0.6.1`.

If you need Smart Turn without downgrading the primary environment, use `.venv-ws3-bench` as the dedicated Smart Turn eval environment.

### Runtime fallback behavior

1. If `pipecat`, `loguru`, `soxr`, or `wait_for2` are missing → `is_available` returns `False` with the exact missing module name.
2. If the specified model path does not exist → constructor sets `unavailable_reason` without crashing.
3. If `cpu_count < 1` → `ValueError` raised immediately in constructor.
4. If `_ensure_analyzer()` fails at runtime → `SmartTurnUnavailableError` raised with chain (`exc`).
5. If arbitration raises during a live turn → `InteractionCore._handle_speech_end` logs `smart_turn_arbitration_failed_fallback_to_vad`, clears candidate buffer, and commits the turn via VAD without audio loss.
6. `handle_barge_in()` clears `_smart_turn_candidate` so hesitation audio from the previous utterance never leaks into a new turn after interruption.

### Known model accuracy limitations (UNDER REVISION)

Smart Turn V3 must not be used as the sole turn endpointer:

- Complete utterances: 40/41 correct (97.6%).
- **Hesitation prefixes: 0/15 correct (100% false-complete rate).**
- Synthetic 60% truncated: 6/41 correct (14.6%).

Smart Turn remains `UNDER REVISION`. It may only be lifted once: (a) hesitation prefix false-complete rate is measurably reduced, and (b) the arbitration path in `InteractionCore._handle_speech_end` (not just `_predict_endpoint`) is validated against hesitation and truncated corpora.

### Benchmark environments

- `.venv`: Production baseline — no Pipecat, pure VAD endpointing. Primary test environment.
- `.venv-ws3-bench`: Smart Turn eval — full Pipecat + onnxruntime 1.24.4 + protobuf 6.33.6.
- `.venv-verify` (disposable): Created during this session to verify fresh `[smart-turn]` reproducibility. `pip check` returned clean. Delete when no longer needed.

## Voice Pipeline Repair (2026-10-21)

**Root Cause 1 — TTS Model Path Misconfiguration**: `config/models.yaml` specified TTS model as HuggingFace repo ID (`hexgrad/Kokoro-82M`) instead of local ONNX file path. `KokoroTTSProvider` requires a local file path. Fixed by updating `models.yaml` to `C:/Users/vishnuu/Projects/TOM/models/kokoro-v0_19.onnx` and adding `voices_path` in `main.py`.

**Root Cause 2 — Audio Feedback Loop**: During TTS playback, the microphone remained active and picked up the assistant's own speech output. VAD detected this as new user speech, triggering immediate barge-in (`LISTENING → PROCESSING → INTERRUPTED → LISTENING` within tens of ms). Fixed by pausing `MicrophoneLoop` during playback (`_on_speaking` callback) and resuming after turn completion (`_on_turn_done`) or barge-in (`handle_barge_in`).

**Root Cause 3 — Smart Turn Dependency Confusion**: Verified that main `.venv` correctly runs without Pipecat (Smart Turn optional). `SmartTurnV3Arbiter.check_is_available()` returns `(False, 'Missing dependency: pipecat')` and `InteractionCore` falls back to pure VAD endpointing. No global Python child process spawned; `onnxruntime` spawns brief `ver` subprocesses (Windows version detection) during TTS model init, not persistent Python processes.

**Files Changed**:

- `config/models.yaml`: Fixed TTS `model_name` to local ONNX path.
- `python/tom/main.py`: Added `voices_path` to `TTSConfig` construction.
- `python/tom/voice/interaction_core.py`: Added `mic.pause()` in `_on_speaking`, `mic.resume()` in `_on_turn_done` and `handle_barge_in`.
- `python/tom/voice/microphone.py`: `resume()` documents buffer clear behavior.
- `tests/unit/voice/test_interaction_core.py`: Updated `interrupt()` helper to resume mic before simulating barge-in.
- `tests/integration/voice/test_interaction_session.py`: Updated barge-in simulation to resume mic.

**Verification**:

- Voice unit suite: **360 passed, 1 skipped** (Smart Turn unavailable).
- Voice integration suite: **31 passed, 4 skipped** (hardware/live offline).
- Total voice tests: **391 passed, 5 skipped**.
- Ruff check/format clean; `pip check` clean; `git diff --check` clean.

## Voice Pipeline Robustness Fix (2026-10-23)

**Observed failures addressed:**

1. Voice STT fails: `Library cublas64_12.dll is not found` with faster-whisper configured for CUDA and `int8_float16`.
2. After STT failure, later turns remained in `PROCESSING` without completing or returning to usable state.
3. Ctrl+C appeared unresponsive while TOM was stuck in blocking operations.
4. IPC disconnect/reconnection during engine stop was not handled cleanly.
5. Endpointing latency metric (previously reported ~1.75–2.11s) was measuring full utterance duration; corrected in Item P3 to measure silence hangover (speech_end - user_speech_stop).

**Fixes implemented:**

### 1. CUDA DLL Failure with Memoized CPU Fallback (`python/tom/voice/stt.py`)

- Added `_cuda_attempted`, `_cuda_failed`, `_cuda_failure_reason` fields to `FasterWhisperSTTProvider`.
- On first CUDA initialization failure (e.g., missing `cublas64_12.dll`), the failure is memoized.
- Subsequent loads skip CUDA entirely and go straight to CPU (`int8`, 4 threads).
- `get_model_info()` now reports `cuda_attempted`, `cuda_failed`, and `cuda_failure_reason` for observability.
- **Tests added**: `TestFasterWhisperCUDAFallback` (3 tests) verifying memoization, no CUDA retry, and CPU-only config behavior.

### 2. STT Error Recovery to LISTENING (`python/tom/voice/interaction_core.py`)

- `_transcribe()` catches all exceptions (except `CancelledError`) and returns empty transcript.
- `execute()` handles empty transcript by returning `TurnStatus.COMPLETED` without starting a turn.
- `_begin_turn()` now defensively resets pipeline from `ERROR` or stuck `SPEAKING` states before starting new turns.
- **Tests added**: `TestSTTErrorRecovery` (3 tests) verifying recovery from `STTUnavailableError`, `STTTranscriptionError`, and multiple consecutive failures.

### 3. IPC Disconnect Handling (`python/tom/voice/streaming_tts.py`, `python/tom/voice/interaction_core.py`)

- `IncrementalTTSStreamer._play_pending()` catches `ConnectionLostError`/`NotConnectedError` during playback and stops gracefully.
- `stop_playback()` handles IPC disconnect during barge-in/shutdown.
- `InteractionCore.handle_barge_in()` and `stop()` catch IPC errors during `stop_audio_playback()`.
- `_run_startup_greeting()` handles IPC errors at each stage (time, LLM, TTS, playback).
- `TurnExecutor._stream_response()` catches IPC errors from TTS streaming.
- **Tests added**: `TestIPCDisconnectionHandling` (3 tests) for playback disconnect, stop disconnect, and barge-in disconnect.

### 4. Ctrl+C / Shutdown Responsiveness via Timeouts (`python/tom/voice/stt.py`, `python/tom/voice/tts.py`)

- `FasterWhisperSTTProvider.transcribe()`: 40s timeout on `asyncio.to_thread` call.
- `KokoroTTSProvider.synthesize()`: 30s timeout on `asyncio.to_thread` call.
- Both use builtin `TimeoutError` (replaced `asyncio.TimeoutError` per Ruff).
- **Tests added**: `TestShutdownResponsiveness` (3 tests) verifying shutdown completes promptly during STT, LLM streaming, and TTS synthesis.

### 5. Defensive State Recovery (`python/tom/voice/interaction_core.py`)

- `_begin_turn()` checks pipeline state before transitioning to `PROCESSING`.
- If pipeline is in `ERROR` state (from previous unhandled exception), resets to `IDLE`.
- If pipeline is stuck in `SPEAKING`, transitions through `INTERRUPTED` to `IDLE`.
- Startup greeting disabled in tests via `startup_greeting=False` parameter.

**Files Changed:**

- `python/tom/voice/stt.py`: CUDA memoization, timeout, model_info reporting
- `python/tom/voice/tts.py`: Timeout on synthesis
- `python/tom/voice/streaming_tts.py`: IPC error handling in playback/stop
- `python/tom/voice/interaction_core.py`: State recovery, IPC error handling, startup greeting flag, test parameter
- `tests/unit/voice/test_stt.py`: 3 CUDA fallback tests
- `tests/unit/voice/test_interaction_core.py`: 15 new tests (STT error recovery, IPC disconnect, shutdown responsiveness)

**Verification:**

- Voice unit suite: **377 passed, 1 warning** (deprecation in Pipecat, unrelated)
- All existing tests pass (377 total)
- Ruff check/format clean; `git diff --check` clean
- Rust tests: **100 passed** (93 unit + 7 integration)

**Remaining risks / follow-up:**

- Endpointing latency metric corrected in Item P3 (now measures silence hangover `speech_end - user_speech_stop`, removing the invalid ~1.75–2.11s utterance-duration metric). Actual hangover is configured at 600ms (observed ~656ms with frame quantisation).
- CUDA fallback is permanent once triggered; no automatic retry (by design).
- Thread-pool timeouts prevent indefinite blocking but don't cancel the underlying C++ inference — accepted tradeoff for responsiveness.

## Phase V1 & V2: Voice Pipeline Repair & Optimization (2026-11-03)

**Items Implemented & Verified in this session:**

1. **P0.1 Root Cause Proven**: Session `3617271b` analysis proved H1. `_on_speaking` gated the mic, but an unhandled playback drain error bypassed `_on_playback_completed`, leaving the mic permanently gated while pipeline transitioned to `LISTENING`.
2. **P0.2 Playback Drain Error Observability**: Wrapped playback drain in ERROR-level logging with traceback, duration, elapsed, timeout, and state (`streaming_tts.py`).
3. **P0.3 Mic Lifecycle Audit**: Complete audit of all call sites of `mic.pause()`, `resume()`, `gate()`, `ungate()`, `mute()`, `unmute()`.
4. **P0.4 LISTENING Invariant Enforced & Hardened Watchdog**: `_run_turn` and `_play_pending` wrapped in `try/finally` guaranteeing idempotent `_on_playback_completed()` execution under all outcomes. Added active `_listening_watchdog` in `InteractionCore` detecting both paused/gated mic in LISTENING > 1.0s and capture starvation (> 5.0s without audio frames arriving), executing recovery without duplicate loops, emitting `microphone_resumed_by_watchdog` or `voice.capture.starvation` at ERROR level, and transitioning to ERROR state if recovery fails.
5. **P0.5 Playback Drain Semantics & Outcome Separation**: Made playback drain non-fatal to mic recovery. Documented lack of engine-side completion signal and wall-clock drain calculation. Separated `playback_completed`, `playback_failed`, and `playback_finalized` outcomes.
6. **P0.6 Complete LISTENING Heartbeat**: Emits `voice.listening.heartbeat` every 5s while in LISTENING with pipeline state, mic status, capture-loop alive, IPC healthy, frames received, RMS, peak, speech-onset age, and time since last frame.
7. **P0.7 Hermetic Tests**: 14 hermetic tests in `tests/unit/voice/test_mic_recovery.py` covering drain failure, cancellation, synthesis failure, IPC disconnect, watchdog recovery, watchdog failure transition, capture starvation detection, starvation recovery, starvation failure transition, playback outcome separation, and 30-turn randomized fault-injection soak (`seed=42`). All 14 passed.
8. **P0.8 Live 15-Turn Test**: `BLOCKED` pending human operator speaking physical acoustic utterances into local microphone.
9. **P1.a First-Chunk Policy**: `SentenceStreamer` flushes speakable first chunks early at clause boundaries or at ~7 words if punctuation is delayed.
10. **P1.b Voice Response Length Cap & Prompt**: `max_response_tokens: 200` added for voice mode while CLI mode remains 1024. `VOICE_SYSTEM_PROMPT` in `prompts.py` updated to limit ordinary replies to at most two short sentences while allowing requested detail.
11. **P1.c Eliminated Lookahead Gap**: Removed blocking `__anext__()` lookahead in `IncrementalTTSStreamer._produce_sentences`, starting synthesis immediately.
12. **P1.d Kokoro ONNX Optimization**: Benchmarked thread counts (8 threads, `allow_spinning=0` achieved optimal median 1366ms on CPU). Status: `FAIL — target missed` (400ms target missed on CPU without hardware acceleration).
13. **P2 Split-Utterance Merging**: When barge-in occurs during `PROCESSING` before playback begins, original and continuation PCM are merged (2s max gap) and transcribed once.
14. **P3 Endpointing Metric Correction**: `endpointing_ms` now measures silence hangover `speech_end - user_speech_stop`.

**Verification:**

- Voice unit suite: **416 passed** (`pytest tests/unit/voice -q --timeout=30`).
- Commits: `9b3924d`, `1f3a06d`, `2bd7b52`, `c8105fb`, `e5011a2`, `6aa0952`, `374d4ed`, `6b27593`, `86af89f`.

## Listening & Sample Capture Fix (2026-10-21)

**Root Causes Identified & Resolved**:

1. **Cumulative Sample Counting**: In `AudioManager`, `captured_samples` was previously calculated as `self.capture_buffer.lock().unwrap().len()`. Because Python's `MicrophoneLoop` polls `get_captured_speech(clear=True)` every 20ms, the ring buffer is continuously drained throughout the session. When `stop_capture()` or `status()` was called, the remaining buffer was empty, erroneously reporting `captured_samples=0`. Added an atomic `total_captured_samples: Arc<AtomicUsize>` counter tracking all samples delivered across the capture session.
2. **CPAL Input Stream Type Safety**: `AudioCaptureController::start_live` previously invoked CPAL's `build_input_stream` with a raw `data: &[u8]` callback and attempted byte casting (`bytemuck::cast_slice`). On Windows WASAPI, passing `u8` requests an 8-bit stream which physical audio devices reject, and misaligned slices could cause panics. Replaced with pattern matching on `native_format` (`F32`, `I16`, `U16`) to build strongly typed CPAL stream callbacks directly.
3. **Loopback Default Device Filtering**: When `device_name` is omitted, if CPAL's default input endpoint resolves to "Stereo Mix" (speaker loopback), the engine now automatically scans for a physical microphone endpoint rather than attempting to capture from the loopback sink.
4. **Resampler Pass-Through**: `conversion_task` now directly passes through frames when the input device sample rate is already 16kHz mono/stereo, eliminating resampling latency and overhead.
5. **Rebuilt Stale Engine Binary**: The old `tom-engine.exe` running in the background was cleanly terminated, and both dev and release binaries were rebuilt fresh.

**Files Changed**:

- [`rust/tom-engine/src/audio/manager.rs`](file:///c:/Users/vishnuu/Projects/TOM/rust/tom-engine/src/audio/manager.rs): Added `total_captured_samples` atomic counter; accurately report total captured samples in `status()` and `stop_capture()`.
- [`rust/tom-engine/src/audio/capture.rs`](file:///c:/Users/vishnuu/Projects/TOM/rust/tom-engine/src/audio/capture.rs): Strongly typed CPAL stream building per native sample format; auto-filter "Stereo Mix" on default device lookup; direct 16kHz pass-through.
- [`config/voice.yaml`](file:///c:/Users/vishnuu/Projects/TOM/config/voice.yaml): Explicit capture device configuration.
- [`python/tom/voice/microphone.py`](file:///c:/Users/vishnuu/Projects/TOM/python/tom/voice/microphone.py): Configured `capture_device` passthrough.
- [`python/tom/main.py`](file:///c:/Users/vishnuu/Projects/TOM/python/tom/main.py): Wire voice config device to microphone loop.

**Verification**:

- Rust engine test suite: **94 passed (87 unit/integration + 7 hardening), 0 failed**.
- Rust capture flow test: verified `captured_samples=7360` (non-zero cumulative capture).
- Python voice unit suite: **360 passed, 1 skipped** (Smart Turn).
- Python IPC unit suite: **161 passed, 0 failed**.
- Release binary built: `rust/tom-engine/target/release/tom-engine.exe` updated.
- Ruff and `graphify update .` clean.

## Next Step

- **Full Repository Audit & Root Directory Cleanup (2026-11-02) COMPLETE.**
  - Full audit report created at `docs/audits/TOM_COMPREHENSIVE_AUDIT_2026-11-02.md`.
  - Root directory cleaned up: 17 logs moved to `logs/`, test scripts to `tests/manual/`, metrics to `scripts/`, environment reports to `docs/environment/`.
- **Immediate Task for Resuming Development:**
  Fix the 4 concrete voice pipeline defects identified during audit:
  1. `python/tom/voice/microphone.py`: in `push_samples()`, allow audio to reach `_process_frame` when gated so barge-in RMS threshold detection can function during `SPEAKING`.
  2. `python/tom/voice/stt.py`: add Windows CUDA DLL directory discovery via `os.add_dll_directory` pointing to `.venv/Lib/site-packages/nvidia/cublas/bin` and `cudnn/bin`.
  3. `python/tom/voice/interaction_core.py`: wrap sentence streamer output with `SpeechFormatter.format()` before yielding to TTS.
  4. `python/tom/voice/latency.py`: fix inverted `_METRIC_STAGES["stt_queue_wait_ms"] = (LatencyStage.SPEECH_END, LatencyStage.STT_DECODE_START)`.
- Follow-up candidate phase:
  - Phase 9.6 Voice Latency Calibration (real hardware benchmark to verify STT drops from 1734ms CPU to <350ms CUDA).
  - Multi-model residency automation (`STAGE3_DESIGN.md`).

## Control Center handoff (2026-11-10)

- The separate app lives at desktop/control-center/; no TOM Python, Rust Engine, voice, or permission code was changed for this integration.
- Use cd desktop/control-center; npm ci; npm run check; npm run build; npm run tauri dev to develop. See ARCHITECTURE.md for packaging and exact feature limits.
- Model binary and GGUF are absent from repository defaults; configure existing local paths before model management. Start TOM currently requires the existing Rust Engine named pipe to pass v1 engine.ping.
- Keep work scoped to follow-up external adapters for STT/TTS/voice observability and tool diagnostics; do not copy TOM's permission implementation into the GUI.

\n

## Control Center Alpha 2 integration ? 2026-11-10 (partial acceptance)

### Changes

- `python/tom/main.py` adds `--service`, authenticated management startup, status/tool/memory exports, voice provider and microphone snapshots, and graceful shutdown integration. Existing `--cli` and `--voice` entry points remain usable. Managed CLI/Voice get the API only when the supervisor provides credentials.
- `python/tom/desktop/control_api.py` implements version 1 loopback routes with a per-launch bearer token, 64 KiB body cap, constant-time authentication, graceful shutdown, SQLite read-only diagnostics, and optional loopback Qdrant health.
- `desktop/control-center/src-tauri/src/main.rs` selects Python modes, generates a random token and loopback port, waits for authenticated Python readiness, requests graceful Python and Engine shutdown before owned-child fallback, exports diagnostics, prompts on window close, and queries NVIDIA telemetry through direct `nvidia-smi` invocation. Full-profile failures roll back only newly started dependencies; healthy model endpoints block duplicate starts.
- `rust/tom-engine/src/ipc/handlers.rs` and `src/main.rs` register `engine.shutdown` with delayed lifecycle-token cancellation so the NDJSON v1 acknowledgment can be sent first.
- Svelte adds explicit Brain Service/CLI/Voice choice, live tool and memory diagnostics, NVIDIA telemetry, and keep/stop/cancel close prompt. The Rust Engine page renders read-only diagnostics on demand through the Tauri command.

### Validation

- `.venv/Scripts/python.exe -m pytest tests/unit/desktop/test_control_api.py tests/unit/desktop/test_wheel_server.py -q` ? **42 passed**. Ruff on modified Python files ? clean.
- Control Center Rust `cargo fmt --check`, `cargo test` ? **3 passed**, `cargo clippy -- -D warnings` ? clean.
- Engine `cargo fmt --check`, full `cargo test` ? **96 unit + 7 integration passed**, `cargo clippy -- -D warnings` ? clean. One earlier concurrent run hit a timing-sensitive audio playback timeout; isolated and subsequent full runs passed.
- `npm run check` ? zero diagnostics; `npm run build` ? passed.
- `npm audit --omit=dev` ? **0 vulnerabilities**.
- `npm run tauri -- build` ? Windows x64 executable, NSIS, and MSI produced. No installer execution or rendered-screen test performed.
- `git diff --check` ? clean. No live TOM runtime services were started or stopped during validation.

### Artifacts

- `desktop/control-center/src-tauri/target/release/tom-control-center.exe`
- `desktop/control-center/src-tauri/target/release/bundle/nsis/TOM Control Center_0.1.0_x64-setup.exe`
- `desktop/control-center/src-tauri/target/release/bundle/msi/TOM Control Center_0.1.0_x64_en-US.msi`

### Remaining Alpha 2 work

Voice model load/unload and functional STT/TTS diagnostics; Engine diagnostics UI; VRAM-based model preflight and allocation policy; expected-model functional readiness; lifecycle transition/operation tracking, Job Objects and process-tree cleanup; complete settings browse/reset, diagnostics export/log filtering, and full component lifecycle pages; live Windows acceptance. Do not call this full Alpha 2 acceptance until these are implemented and verified.

## Control Center Alpha 2.1 update (2026-11-10)

Added authenticated voice status/load/unload/microphone/TTS diagnostic actions using TOM's existing provider objects; provider lifecycle operations are serialized and an active Voice pipeline blocks independent actions. CUDA STT admission now requires available GPU telemetry and ResourceManager budget, with automatic CPU fallback disabled for management operations. Native worker timeout state protects models from unload while inference may remain active. Logical unload is reported separately from unconfirmed physical memory release. Engine playback unit coverage uses deterministic mocked playback.

Validation: focused Python suite 115 passed; Ruff clean; Engine 96 unit + 7 integration tests and Clippy passed; Tauri 4 unit tests and Clippy passed; Svelte check clean; npm production audit 0 vulnerabilities. Windows package rebuild and overall integration validation are pending. No live microphone/audio or TOM service acceptance was performed.

Remaining: Windows Job Objects/process-tree ownership, persistent lifecycle history and operation IDs, native path browsing/reset, richer log filtering/export, system-wide GPU admission/preflight, and live Windows/hardware acceptance. Alpha 2.1 is partial until these are completed.

## Control Center Runtime Profile Redesign — WIP Checkpoint (2026-11-11)

**Status: WIP — IMPLEMENTATION PARTIAL; NOT ACCEPTED.**

The November 10 Control Center iterations established the initial authenticated Python management API, voice-provider controls, Rust process supervision, and Svelte dashboard. The subsequent runtime-profile redesign was interrupted before end-to-end implementation and acceptance.

The current working tree must be treated as an unfinished development checkpoint.

### Implemented in this checkpoint

- Authenticated, loopback-only Python management API with per-process bearer credentials.
- Python management diagnostics for Engine, model, Voice providers, tools, and memory.
- CLI input injection and stdout/stderr capture.
- Shared lazy STT and Kokoro provider construction.
- Voice pipeline start/stop through the management API.
- STT CUDA admission against the existing process-local ResourceManager.
- Command Wheel Python backend, AHK integration, request validation, bounded concurrency, and duplicate-request protection.
- Rust Engine graceful shutdown IPC operation.
- Initial Tauri 2/Svelte Control Center with process diagnostics, telemetry, Voice controls, and local configuration.

### Runtime profile redesign — outstanding defects

1. `RUNTIME_PROFILES` declares CLI, CLI Debug, CLI Verbose, Voice, Voice Debug, Voice Verbose, and Service profiles, but the actual process specification and launch commands do not implement these profiles end-to-end.
2. `start_component` stores Python sessions under raw IDs such as `cli`, while session enumeration and lookup expect `runtime:<profile_id>` keys. This breaks session discovery, ownership checks, and diagnostics. It risks overwriting ownership records for still-running children.
3. Multiple independent Python runtime sessions are not implemented. The current ownership checks are also unreliable because of the registry-key mismatch.
4. `start_lock` is declared but not used to serialize process startup.
5. CLI readiness relies on authenticated management API readiness. The functional CLI input/output ping-pong handshake is not implemented in the Rust supervisor.
6. Voice is still managed inside the Python Service/CLI architecture. Independent Voice process profiles are not implemented.
7. Global GPU admission and microphone ownership across independent Python processes are not implemented.
8. The frontend still contains the Python Runtime navigation entry and Planned Capabilities section. It does not implement the requested runtime-profile dashboard hierarchy.
9. Command Wheel backend ownership and lifecycle are not fully integrated into one supervisor-owned logical component.
10. Live Windows process interaction, independent session operation, microphone capture, and hardware acceptance remain unverified.

### Security and functional limitations

- Command Wheel Note/Reminder confirmation currently uses an AHK confirmation dialog and a client-supplied `approved` field. The backend does not independently authenticate the origin of that approval.
- OCR is explicitly deferred in the current wheel implementation.
- No global cross-process GPU reservation mechanism is available.
- No completed multi-session CLI interaction demonstration exists.

### Validation actually performed for this checkpoint

- `tests/unit/resources/test_governor_integration.py`: 17 passed on 2026-11-11.
- `git diff --check`: no patch errors; line-ending warnings remain.
- Earlier scoped Control Center, Python, Rust, Svelte and Vite results are documented in the November 10 entries. They must not be interpreted as acceptance of the unfinished November 11 runtime-profile redesign.

Historical `governorlog.txt` and `testlog.txt` contain failures from earlier test executions. The current focused governor test run passes.

### Resume instructions

Resume from the current implementation. Do not recreate already implemented management API, Voice provider, AHK wheel, or Control Center functionality.

Recommended repair order:

1. Correct Rust session registry keys and ownership invariants.
2. Implement actual runtime profile specifications and independent launch/stop/restart operations.
3. Implement synchronized startup and runtime compatibility/resource admission.
4. Implement functional CLI readiness and per-session input/output routing.
5. Implement independent Voice runtime profiles and microphone ownership.
6. Consolidate Command Wheel backend lifecycle.
7. Complete the requested dashboard hierarchy and remove generic Python Runtime and Planned Capabilities presentation.
8. Add deterministic process/IPC integration tests.
9. Complete focused validation and Windows acceptance.

**Git checkpoint:** All legitimate current implementation work is to be preserved in a WIP commit on `main`. No destructive reset, clean, or force-push. The intended remote is `origin/main`.
