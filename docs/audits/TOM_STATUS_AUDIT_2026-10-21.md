# TOM Status Audit Report

**Date:** 2026-10-21  
**Auditor:** Antigravity (investigation-only — no application files modified)  
**Ponytail mode:** full

---

## 1. Executive Summary

TOM is a feature-rich, well-structured local-first AI assistant. Phases 0–14 are documented as complete. The code matches the documentation in broad strokes, but **voice is not verifiably working end-to-end** because:

1. `pipecat-ai` is installed but **broken** — its `__init__.py` imports `loguru` which is **not installed**, crashing every real pipecat import. `SmartTurnV3Arbiter` silently falls back to `COMPLETE` (line 932 of `interaction_core.py`) so a live session degrades silently rather than hard-crashing.
2. The Rust `tom-engine` is **not running** in this environment; named pipe `\\.\pipe\tom-engine` absent. All voice, audio, and IPC functionality requires it at runtime.
3. Two integration tests for `FasterWhisperSTTProvider` and `KokoroTTSProvider` are **failing**: they expect "not installed" errors but both libraries ARE installed, causing different error messages.
4. Phase 9.5 (live event loop integration) and Phase 9.6 (latency calibration) are **intentionally unstarted** per HANDOFF.md.
5. The full-repo baseline (1301 tests) has **not been rerun** since the Phase 8 exit on 2026-10-02.

**Overall health:** Code quality and test coverage are excellent. The project is at an advanced but not complete stage. Voice mode requires engine, models, and a pipecat fix to be operational.

---

## 2. Repository Baseline and Git State

| Item | Value |
|---|---|
| Branch | `main` |
| HEAD | `21ad589` ("Stabilize TOM IPC and desktop integration") |
| Remote sync | `origin/main` — up to date |
| Staged changes | None |
| Tracked modifications | None |
| Untracked files | `pip-freeze-before-pipecat.txt`, `pipecat-install-plan.json` |
| Python version | 3.11.9 |
| Virtual env | `.venv` |
| Additional envs | `.venv-ws2-bench`, `.venv-ws3-bench` (benchmark-only) |

### Recent Commits (last 10)

```
21ad589  Stabilize TOM IPC and desktop integration          (HEAD, main)
41873bc  feat(resources): integrate priority-based resource arbitration
b4550c7  chore: stop tracking voice recordings
5331c55  fix(tests): make tests/manual/__init__.py a plain empty file
ac8d6d8  docs(phase10): closeout; remove PHASE10_IMPLEMENTATIONPLAN
469bca5  test(phase9): smart turn arbitration, interaction session, hardware latency tests
1afaa70  feat(phase10): web fetch/search, personal tools, SSRF hardening
66da635  feat: add local web search and fetch tools
62a81ca  chore: finalize phase 9 and consolidate on llama.cpp
5c3e72e  docs(phase9): checkpoint 1 fix round audited results
```

Commit `21ad589` touched 44 files: `main.py` (new, 644 lines), IPC client/transport stability, tray daemon, health check script, e2e test scaffolding, and smart-turn test guards.

---

## 3. Planning Document Reconciliation

| Document | Claims | Assessment |
|---|---|---|
| `IMPLEMENTATIONPLAN.md` | Phases 0–14 defined | Planning doc. Matches implemented architecture. |
| `plan.md` | Detailed phase design | Broadly implemented; voice Phase 9.5/9.6 and "Phase 13 Benchmarking" remain unstarted. |
| `STATE.md` | Phases 0–14 complete; next = "Phase 13 — Agent Evaluation" | **Partially misleading.** Phase 9.5/9.6 are explicitly deferred; voice is not end-to-end verified. |
| `PROGRESS.md` | Phase ledger shows all phases closed with test counts | Test counts are from targeted runs, not full-repo re-runs. Accurate for what was run. |
| `HANDOFF.md` | Pipeline assembled 9.0→9.4; 9.5 is the first live binding; requires explicit approval | **Accurate and honest.** Most reliable state document. |

**Key discrepancy:** STATE.md creates a false impression that voice is fully operational. HANDOFF.md explicitly notes 9.5 is not done. Both can be true simultaneously (9.5 was never marked complete), but the framing is misleading.

---

## 4. Roadmap Phase-by-Phase Status Matrix

| Phase | Description | Status | Evidence |
|---|---|---|---|
| 0 | Foundation / Bootstrap | **VERIFIED COMPLETE** | Directory structure, `pyproject.toml`, logging, config all exist and import cleanly. |
| 1 | Rust Engine (`tom-engine`) | **IMPLEMENTED, NOT VERIFIED** | `rust/tom-engine/` exists; not running; named pipe absent. Cargo tests: 83 passing (historical). |
| 2 | Python IPC Client | **IMPLEMENTED, NOT VERIFIED** | `python/tom/ipc/` — 161 unit tests pass now (confirmed). Live IPC test fails: pipe absent. |
| 3 | Tools & Permission Engine | **VERIFIED COMPLETE** | 34 tools registered; `ToolExecutor` + `PermissionEngine` + `PathPolicy` unit-tested. |
| 4 | Local LLM / Model Infrastructure | **IMPLEMENTED, NOT VERIFIED** | `HttpModelProvider` wraps llama.cpp. LLM endpoint offline. Unit tests with mock provider pass. |
| 5 | Router & Task Lifecycle | **VERIFIED COMPLETE** | `IntentRouter`, `TaskState` machine, `CancellationToken` implemented and unit-tested. |
| 6 | Agents & Planning | **VERIFIED COMPLETE** | `AgentOrchestrator`, `Agent`, `TaskPlanner`, `TaskExecutor` all implemented; 264 agent unit tests pass. |
| 7 | Memory Architecture | **VERIFIED COMPLETE** | `MemoryManager` (SQLite + Qdrant optional), 5 memory tools, unit tested. |
| 8 | Voice Pipeline (base) | **IMPLEMENTED, NOT VERIFIED** | `VoicePipelineManager`, `STTProvider`, `TTSProvider`, `SpeechFormatter`, barge-in all exist. Models absent. |
| 9.0–9.4 | Voice Interaction Core | **IMPLEMENTED, NOT VERIFIED** | All components exist. 351 voice unit tests pass. End-to-end not run (requires engine + models). |
| 9.5 | Live Event Loop Integration | **NOT STARTED** | Explicitly deferred. `InteractionCore` is wired in `main.py` but epoch invalidation/turn recovery not implemented. |
| 9.6 | Latency Calibration | **NOT STARTED** | Deferred. Tracer infrastructure exists; calibration work unstarted. |
| 10 | Web & Personal Tools | **VERIFIED COMPLETE** | 34 canonical tools, SSRF-safe web tools, SQLite personal tools, all unit-tested. |
| 11 | Security Hardening | **VERIFIED COMPLETE** | `SecretScrubber`, `PathPolicy`, `ConsoleConfirmationHook`, `SecurityAuditLogger`, adversarial tests all passing. |
| 12 | Concurrency & Resource Management | **VERIFIED COMPLETE** | `ResourceManager`, `ModelLifecycleCoordinator`, `PriorityResourceQueue` implemented, 100-cycle stress tests passing. |
| 13 (plan) | IPC Stabilization | **VERIFIED COMPLETE** | Commit `21ad589`; IPC reconnect reliability, health check, tray. 161 IPC unit tests pass. |
| 14 (plan) | Tray / Desktop Integration | **VERIFIED COMPLETE** | `TrayDaemon`, `TextConsole`, `main.py` with CLI/voice/health modes, 33 tray tests passing. |
| Original Phase 13 | Agent Evaluation & Benchmarks | **NOT STARTED** | Explicitly deferred, awaiting approval per HANDOFF.md. |

---

## 5. Voice Pipeline Investigation

### Finding: PARTIALLY CONFIRMED BROKEN

The pipeline is architecturally complete but cannot run end-to-end without external dependencies (engine, models, working pipecat).

### Pipeline Trace

```
audio  →  MicrophoneLoop(9.0)  →  SpeechStart/Frame/End
       →  InteractionCore._handle_speech_end()
       →  SmartTurnV3Arbiter.analyze()          ← BROKEN (pipecat/loguru)
       →  TurnExecutor.execute()
       →  StreamingSTTProvider (FasterWhisper)   ← model file unverified
       →  ConversationTurnManager + ResponsePolicy
       →  AgentOrchestrator.stream()             ← LLM endpoint offline
       →  SentenceStreamer → StreamingTTSProvider
       →  IncrementalTTSStreamer → EngineClient.play_audio_buffer()  ← engine absent
       →  EngineClient.stop_audio_playback()
```

### Stage-by-Stage Assessment

| Stage | Status | Evidence |
|---|---|---|
| **1. Microphone / device selection** | NOT VERIFIED | `MicrophoneLoop` polls `EngineClient.get_speech()`. Engine absent → fails on first frame. |
| **2. VAD / energy detection** | IMPLEMENTED | `EnergyVADDetector` in `vad.py`; 17 microphone unit tests pass. |
| **3. STT transcription** | PARTIALLY BROKEN | `faster-whisper==1.2.1` IS installed. Integration test confirms this. Model path hard-coded in `main.py:446`; directory `models/` exists but contents unverified. |
| **4. SmartTurn arbitration** | **BROKEN** | `pipecat-ai 1.12.0` installed but `import pipecat` raises `ModuleNotFoundError: No module named 'loguru'`. `_handle_speech_end()` try/except (line 926–932) falls back to `COMPLETE` — turns proceed but SmartTurn is silently non-functional. |
| **5. STT → turn finalization** | IMPLEMENTED | `TurnExecutor._transcribe()` logic correct; 351 voice tests pass against mocks. |
| **6. LLM stream / orchestration** | NOT VERIFIED | `AgentOrchestrator.stream()` exists; requires `http://127.0.0.1:65400/v1`. It is not running. |
| **7. TTS generation** | PARTIALLY BROKEN | `kokoro-onnx==0.6.1` IS installed but model files absent. `kokoro-v0_19.onnx` and `voices.bin` not present in project root. Integration test confirms "File doesn't exist". |
| **8. Audio playback** | NOT VERIFIED | `EngineClient.play_audio_buffer()` → named pipe → `AudioManager` in Rust. Engine absent. |
| **9. Barge-in / cancellation** | IMPLEMENTED | `TurnEpochGuard`, `PlaybackStopGuard`, `CancellationToken` all implemented; barge-in tests pass (mocked). |
| **10. Lifecycle / shutdown** | IMPLEMENTED | `InteractionCore.stop()` and `TomApp._shutdown()` both clean. |

### SmartTurnV3Arbiter Detail

- **File:** `python/tom/voice/smart_turn.py`
- **Wired in main.py:** `turn_arbitrator = SmartTurnV3Arbiter()` at line 469 (no analyzer injected → lazy load)
- **Fallback:** `interaction_core.py:926–932` — exception in `analyze()` → logs warning + returns `COMPLETE`
- **Root cause:** `pipecat-ai` installed without `loguru` dependency. `pip show pipecat` says "not found" (registered as `pipecat-ai`).
- **Tests:** Both arbitration tests guarded by `pytest.importorskip("pipecat")` and **skip cleanly** (2 skipped). `test_smart_turn_lifecycle.py` uses mock arbiter and passes (2/2).

### Missing Runtime Assets

| Asset | Required by | Status |
|---|---|---|
| `models/faster-whisper-small.en/` | `FasterWhisperSTTProvider` | **Unknown** — directory exists, contents unverified |
| `kokoro-v0_19.onnx` | `KokoroTTSProvider` | **ABSENT** — confirmed by integration test |
| `voices.bin` | `KokoroTTSProvider` | **ABSENT** |
| `\\.\pipe\tom-engine` | All audio/IPC | **ABSENT** — engine not running |
| `http://127.0.0.1:65400/v1` | LLM inference | **ABSENT** — llama.cpp not running |

---

## 6. IPC and Rust/Python Integration Assessment

### Architecture

- Transport: `NamedPipeTransport` (`python/tom/ipc/transport.py`) over `\\.\pipe\tom-engine`
- Client: `NamedPipeIpcClient` (`python/tom/ipc/client.py`) with async request correlation
- Engine: `EngineClient` (`python/tom/core/engine.py`) wraps `NamedPipeIpcClient` with typed methods
- Protocol: Length-prefixed JSON (`IpcRequest` / `IpcResponse` Pydantic models)

### Stability Work (commit `21ad589`)

- 163 lines changed in `client.py`: reconnect cycle tests, connection state guards
- 20 lines changed in `transport.py`: EOF/disconnect handling
- 409 new lines in `tests/unit/ipc/test_client.py`: reconnection cycles, state machine tests

### Test Results

- **Unit IPC tests:** **161 passed** (confirmed in this audit)
- **Live IPC test (`tests/integration/python_rust/test_live_ipc.py`):** **1 FAILED** — engine not running. Additionally: `readuntil() called while another coroutine is already waiting for incoming data` — this is a **test-isolation bug** (test calls `transport.read_frame()` directly while `_receive_loop` is running), not a production bug.

### Known Limitations (per HANDOFF.md)

- Software VRAM reservations are not guaranteed physical ceilings
- `aclose()` on streaming generator does not guarantee server-side CUDA deallocation
- Model eviction is bookkeeping, not proof of physical engine unload

These are documented, honest architectural limitations.

---

## 7. Other Subsystem Findings

### Security
- `SecretScrubber`, `PathPolicy`, `ConsoleConfirmationHook`, `SecurityAuditLogger` all implemented
- Security adversarial tests: **101 passed** (confirmed in this audit)
- `mss.mss` deprecation warning at `vision/capture.py:70` — benign

### Configuration
- `config/` YAML templates exist
- LLM endpoint `http://127.0.0.1:65400/v1` hard-coded (acceptable for local-first)
- STT model path in `main.py:446` hard-coded as `C:/Users/vishnuu/Projects/TOM/models/faster-whisper-small.en` — **not configurable via YAML**

### Desktop / Tray
- `TrayDaemon`: 33 unit tests pass
- `TextConsole` CLI mode: implemented
- `main.py`: 644-line unified entry point with `--voice`, `--cli`, `--health`, `--headless`, `--no-engine` modes

### Memory
- `MemoryManager` with SQLite primary, optional Qdrant — implemented
- Qdrant not installed (optional dep, graceful degradation expected)

### Vision
- `VisionManager`, `ScreenCaptureService`, `PrivacyShield` — implemented
- `opencv-python` not installed — 4 vision tests skip, rest pass
- `winocr` not installed — 4 OCR tests skip

### Dependency Status: `pipecat-install-plan.json`

This is a `pip install --report` JSON artifact (pip report format v1) taken when pipecat-ai was installed. The install was executed (`pipecat-ai 1.12.0` is in `.venv`), but `loguru` — a required runtime dependency — was not installed. This indicates the install was done with `--no-deps` or `loguru` was excluded. `pyproject.toml` does not declare `pipecat-ai` or `loguru` anywhere.

---

## 8. Test Evidence from This Audit Session

| Suite | Result |
|---|---|
| `tests/unit/voice/test_smart_turn_*.py` (3 files) | **3 passed, 1 skipped** (pipecat not importable) |
| `tests/unit/ipc/` | **161 passed** |
| `tests/unit/voice/` (all) | **351 passed, 2 skipped** |
| `tests/unit/desktop/` | **33 passed** |
| `tests/unit/` (all) | **~1580+ passed, 10 skipped, 0 failed** |
| `tests/integration/` (excl. python_rust) | **~175+ passed, 5 skipped, 2 FAILED** |
| `tests/integration/python_rust/` | **1 FAILED** (engine absent) |
| `tests/security/` | **101 passed** |

### Failures Observed

| Test | File | Failure | Root Cause |
|---|---|---|---|
| `test_faster_whisper_unavailable_dependency_handling` | `test_voice_pipeline.py:593` | Did not raise `STTUnavailableError` | `faster-whisper` IS installed — test premise stale |
| `test_kokoro_unavailable_dependency_handling` | `test_voice_pipeline.py:619` | Regex mismatch: "kokoro-onnx is not installed" vs "File doesn't exist" | `kokoro-onnx` IS installed, model file absent — different error path |
| `test_live_version_mismatch_returns_remote_error` | `test_live_ipc.py:260` | `ConnectionLostError` + concurrent read error | Engine not running; test-isolation bug |

### Skipped Tests

| Reason | Count |
|---|---|
| `pipecat` not importable (no `loguru`) | 2 |
| `opencv-python` not installed | 4–5 |
| `winocr` not installed | 4 |
| Engine IPC unavailable | 1 |
| LLM endpoint unavailable | 2 |
| Test audio file (`test_med.wav`) absent | 2 |

---

## 9. Prioritized Blocker Register

### P0 — Critical / Blocks Voice Operation

| ID | Affected | Symptom | Root Cause | Proposed Fix |
|---|---|---|---|---|
| **P0-1** | `SmartTurnV3Arbiter` | `import pipecat` crashes: `No module named 'loguru'` | `pipecat-ai` installed without `loguru` | `.venv\Scripts\pip install loguru` |
| **P0-2** | TTS | `KokoroTTSProvider` raises "File doesn't exist" | `kokoro-v0_19.onnx` and `voices.bin` absent | Download model files to project root |
| **P0-3** | STT | STT may fail if `models/faster-whisper-small.en` is absent | Model not downloaded or path misconfigured | Verify/download model; make path configurable |
| **P0-4** | Voice/IPC/Audio | Named pipe `\\.\pipe\tom-engine` absent | `tom-engine` not running | Build (`cargo build --release`) and start the engine binary |

### P1 — High / Tests Failing

| ID | Affected | Symptom | Root Cause | Proposed Fix |
|---|---|---|---|---|
| **P1-1** | `test_voice_pipeline.py:593` | Did not raise `STTUnavailableError` | Test assumes `faster-whisper` not installed — it is | Update test to test bad model path instead |
| **P1-2** | `test_voice_pipeline.py:619` | Regex mismatch on `TTSUnavailableError` message | `kokoro-onnx` is installed; error is "File doesn't exist" not "not installed" | Update test regex to match current error path |
| **P1-3** | `test_live_ipc.py:260` | Concurrent-read asyncio error | Test calls `transport.read_frame()` directly while `_receive_loop` is running | Fix test: don't read transport directly when receive loop is active |

### P2 — Medium / Incomplete or Deferred

| ID | Affected | Symptom | Proposed Fix |
|---|---|---|---|
| **P2-1** | Voice (Phase 9.5) | No live event loop integration; epoch invalidation/turn recovery missing | Run Phase 9.5 after P0 blockers resolved |
| **P2-2** | Vision | `mss.mss()` deprecated at `vision/capture.py:70` | `self._sct = mss.MSS()` (one-line fix) |
| **P2-3** | STT config | Hard-coded model path in `main.py:446` | Move to `config/models.yaml` |
| **P2-4** | Test baseline | Full suite not re-run since 2026-10-02 | Run `pytest tests/` after P1 fixes |

### P3 — Low / Quality

| ID | Symptom | Proposed Fix |
|---|---|---|
| **P3-1** | `pipecat-ai` + `loguru` not declared in `pyproject.toml` | Add to optional group `voice-smart-turn` or remove if unready |
| **P3-2** | STATE.md misleadingly implies voice is operational | Add note: "Voice requires engine running and model files present. 9.5/9.6 not started." |
| **P3-3** | Both SmartTurn arbitration tests skip (pipecat broken) | Resolve P0-1 → tests will run |
| **P3-4** | Qdrant not installed; memory SQLite-only | Document the fallback in README |

---

## 10. Recommended Implementation Sequence

**Task 1 — Fix pipecat-ai / loguru (P0-1) — ~5 min**

```powershell
.venv\Scripts\pip install loguru
.venv\Scripts\python.exe -c "import pipecat; print('OK:', pipecat.__version__)"
.venv\Scripts\python.exe -m pytest tests\unit\voice\test_smart_turn_arbitration.py tests\unit\voice\test_smart_turn_arbitration_real.py -v
```

Accept: both tests pass (currently 2 skipped).

**Task 2 — Fix two stale integration tests (P1-1, P1-2) — ~30 min**

Files: `tests/integration/voice/test_voice_pipeline.py` lines 580–621  
Accept: `pytest -k "unavailable"` → 2 passed.

**Task 3 — Fix live IPC test race condition (P1-3) — ~20 min**

Files: `tests/integration/python_rust/test_live_ipc.py` around line 260  
Accept: test removed, properly skipped, or fixed to not race on transport reads.

**Task 4 — Download model files (P0-2, P0-3) — ~15 min**

Download `kokoro-v0_19.onnx` + `voices.bin`; verify `models/faster-whisper-small.en/` contents.  
Accept: `KokoroTTSProvider().check_health()` returns True.

**Task 5 — Start tom-engine (P0-4) — ~30 min**

```powershell
cd rust\tom-engine; cargo build --release; .\target\release\tom-engine.exe
```

Accept: `\\.\pipe\tom-engine` exists; Python IPC client connects.

**Task 6 — Full-repo test re-baseline (P2-4) — ~5 min**

```powershell
.venv\Scripts\python.exe -m pytest tests/ --ignore=tests/integration/python_rust -q --tb=short
```

Accept: 0 failures; ≥1301 passed.

**Task 7 — Fix mss deprecation (P2-2) — ~2 min**

`vision/capture.py:70`: `self._sct = mss.MSS()`

**Task 8 — Phase 9.5 Live Integration — multi-session**

Requires Tasks 1–5. Real mic → STT → LLM → TTS → playback end-to-end.

---

## 11. Exact Next Task

**Task 1: `pip install loguru`** — 5-minute, zero-risk fix. Unblocks 2 skipped tests and enables SmartTurn in voice mode.

Acceptance criteria for Task 1:
1. `python -c "import pipecat"` exits without error
2. `pytest tests/unit/voice/test_smart_turn_arbitration.py tests/unit/voice/test_smart_turn_arbitration_real.py -v` shows **2 passed** (was: 2 skipped)
3. `pytest tests/unit/voice/ -q` still shows 351 passed (no regression)

---

## 12. Unresolved Questions

1. **Is `models/faster-whisper-small.en/` correctly populated?** Directory exists, contents not verified.
2. **Was pipecat-ai installed with `--no-deps`?** `pipecat-install-plan.json` shows planned deps but `loguru` absent from venv.
3. **Does `tom-engine` compile cleanly on current Rust toolchain?** Rust source exists; compilation not attempted.
4. **Phase 9.5 scope:** HANDOFF.md: "epoch invalidation and turn recovery are still unimplemented — 9.5 owns them." These are **missing features** in the current `interaction_core.py`, not just untested code.
5. **Phase 9.6 latency calibration:** Tracer infrastructure present; baseline calibration numbers missing.
6. **`STAGE3_DESIGN.md`:** 285-line file added in commit `21ad589`. Not read during this audit.

---

*Investigation-only pass. No application code modified. Only `TOM_STATUS_AUDIT.md` created.*
