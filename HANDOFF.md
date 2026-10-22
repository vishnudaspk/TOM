# Handoff — Phase 14 Tray Mode & Desktop Integration COMPLETE

Final architecture: TOM uses llama.cpp at `http://127.0.0.1:65400/v1` through the generic OpenAI-compatible `HttpModelProvider`. Bionic/LM Studio is not an active runtime dependency.

Invariants, hardware, and architecture: **`STATE.md`**. Phase ledger and ADRs 041–062: **`PROGRESS.md`**. The temporary Phase 12 implementation plan was removed after closeout.

## Resume

- **Done:** Phase 9 Iterations **9.0–9.4** (VAD/endpointing, streaming STT, LLM stream/sentences, streaming TTS/playback, turn manager/response policy) and applicable 9.B checkpoint work.
- **Done:** Phase 10 **Iterations 1–7** (34 canonical tools registered, schema export, data-driven permissions, SSRF-safe `web.search`/`web.fetch`, SQLite personal tools, ephemeral `vision.analyze`, exit gate verified).
- **Done:** Phase 11 **Iterations 11.1–11.6** (Secret scrubbing via regex catalog + Shannon entropy heuristic, centralized `PathPolicy` jail & sensitive file blacklist, stdin-safe confirmation hook & anti-flooding rate limits, structured `SecurityAuditLogger`, `ToolExecutor` input/output/error scrubbing with typed `BaseModel` preservation, 101 concrete adversarial tests, and secret scanner benchmark).
- **Done:** Phase 12 **Iterations 12.1–12.5 CLOSED & COMPLETE** (Atomic Resource Governor, Engine Telemetry Cache, Model Lifecycle Coordinator, Priority Resource Queue Arbitration & Cooperative Preemption, Adversarial Concurrency Stress Suite with 100 cycles, and Focused Validation Gate).
- **Done:** Phase 13 **IPC Stabilization, Tool Grounding & Runtime Fixes** (IPC idle/reconnect reliability, engine health accuracy, tool grounding/anti-fabrication, startup constructor fix).
- **Done:** Phase 14 **Tray Mode & Desktop Integration** (System tray daemon with pystray, engine connection status monitoring, mute toggle, CLI/voice mode support).
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

| Piece | Path |
|---|---|
| Events / VAD / PCM | `python/tom/voice/events.py`, `vad.py`, `audio_utils.py`, `microphone.py` |
| STT | `python/tom/voice/streaming_stt.py` (`StreamingSTTProvider`, `FasterWhisperStreamingAdapter`, `PartialTranscript`) |
| LLM stream / sentences | `python/tom/agents/orchestrator.py` (`stream()`), `python/tom/voice/sentence_streamer.py` |
| TTS + playback | `python/tom/voice/streaming_tts.py` (`StreamingTTSProvider`, `IncrementalTTSStreamer`, `SynthesisChunk`) |
| Turn state / policy | `python/tom/schemas/turn.py`, `python/tom/voice/turn_manager.py`, `python/tom/voice/response_policy.py` |
| Exports | `python/tom/voice/__init__.py` |
| Tests | `tests/unit/voice/test_microphone_loop.py` (17), `test_streaming_stt.py`, `tests/unit/agents/test_orchestrator_streaming.py`, `test_sentence_streamer.py`, `test_streaming_tts.py` (18), `test_turn_manager.py` (29), `test_response_policy.py` (32) |
| WS0/WS1 Benchmarks | `tests/benchmarks/voice/benchmark_stt_tuning.py`, `tests/benchmarks/voice/results/stt_tuning_results.json` |
| WS0 Hardware Test | `tests/integration/voice/test_hardware_latencies.py::test_real_stt_first_partial_meets_sla` |

## Phase 10 Tools (Iterations 1–5)

| Tool | Path | Description |
|---|---|---|
| `web.search` | `python/tom/tools/web.py` | Local SearXNG search; bounded results; SAFE |
| `web.fetch` | `python/tom/tools/web.py` | SSRF-safe fetch; redirect validation; HTML extraction; SAFE |
| `personal.note` | `python/tom/tools/personal.py` | Note CRUD; SQLite-backed; retrieve SAFE / mutations ASK_USER |
| `personal.calendar` | `python/tom/tools/personal.py` | Calendar event CRUD; UTC timestamps; retrieve SAFE / mutations ASK_USER |
| `personal.reminder` | `python/tom/tools/personal.py` | Reminder CRUD + recurrence + ProactiveScheduler lifecycle; retrieve SAFE / mutations ASK_USER |
| `vision.analyze` | `python/tom/tools/vision.py` | Ephemeral screen/crop analysis via VisionManager (OCR, CV, VLM); SAFE |

| Tests | Path |
|---|---|
| Unit web search | `tests/unit/tools/test_web_search.py` (8) |
| Unit web fetch | `tests/unit/tools/test_web_fetch.py` (27) |
| Integration tools | `tests/integration/tools/test_tool_pipeline.py` (223) |
| Personal store | `tests/unit/personal/test_personal_store.py` (39) |
| Personal reminder service | `tests/unit/personal/test_reminder_service.py` (17 async) |
| Personal permissions | `tests/unit/personal/test_personal_permissions.py` (15) |
| Vision tools | `tests/unit/tools/test_vision_tools.py` (26) |

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
- **Phase 14 (Tray Mode & Desktop Integration) COMPLETE.** All targeted fixes validated.
- **Voice Pipeline Repair (2026-10-21) COMPLETE.** Feedback loop eliminated, TTS config fixed, VAD fallback verified.
- Next candidate phase:
  - Phase 13 — Agent Evaluation, Benchmarks & Hardening (the original Phase 13 roadmap item).
  - Phase 9.5/9.6 (Voice Pipeline Integration & Latency Calibration).
- **Both require explicit user approval before starting.** Do not implement in this session.