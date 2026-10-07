# TOM Development Progress

## Ledger

| Phase | Focus | Status | Verified | Closed |
|---|---|---|---|---|
| 0 | Repo / toolchains | CLOSED | Verified | 2026-09-12 |
| 1 | `tom-engine` IPC + telemetry | CLOSED | 79 Rust | 2026-09-13 |
| 2 | Python core + Named Pipe client | CLOSED | 168 | 2026-09-14 |
| 3 | Tools, permissions, sandbox | CLOSED | 406 | 2026-09-17 |
| 4 | Agents, lifecycle, local routing | CLOSED | 621 | 2026-09-19 |
| 5 | Memory (SQLite + optional Qdrant) | CLOSED | 621 | 2026-09-24 |
| 6 | Voice (audio IPC, STT, TTS, barge-in) | CLOSED | 931 (848 Py + 83 Rust) | 2026-09-30 |
| 7 | Vision + OS input | CLOSED | 1087 (1004 Py + 83 Rust) | 2026-10-01 |
| 8 | Proactive agent + long-horizon tasks | CLOSED | **1301** (1218 Py + 83 Rust), 8 skipped | 2026-10-02 |
| 9 | Interaction core & voice experience | **CLOSED FOR 9.0–9.4** | 9.0–9.4 verified; targeted 833 passed; voice suite 317 green; full repo not re-run | 2026-10-07 |

## Current baseline

- **Last full-repo run**: 2026-10-02 Phase 8 exit — 1218 Python (1048 unit + 170 integration) + 8 skipped (cv2/winocr) + 83 Rust = **1301**. Quality: Ruff check/format clean; Cargo fmt/clippy clean.
- **Since then**: Phase 9.0–9.4 added 68 voice unit tests. Targeted runs green — `tests/unit/voice` + `tests/integration/voice` = **317**; with agents/core/ipc = **833**. Full suite not re-measured.

## Closed phases (milestone only)

| Phase | Delivered (still in use) |
|---|---|
| 0 | Dual workspace, `.venv`, Cargo, Ruff/Pytest |
| 1 | Tokio `tom-engine`, `\\.\pipe\tom-engine`, telemetry, event bus |
| 2 | `NamedPipeIpcClient`, `EngineClient`, reconnect |
| 3 | `ToolRegistry` / `ToolExecutor` / `PermissionEngine` (SAFE / ASK_USER / BLOCK), `PathGuard` |
| 4 | 6-state `Agent`; `AgentOrchestrator`; `LLMProvider`; `CancellationToken` |
| 5 | `MemoryManager`; SQLite authority; optional Qdrant; CPU embeddings; 5 memory tools |
| 6 | Rust `AudioManager`; FasterWhisper STT; Kokoro TTS; `SpeechFormatter`; `VoicePipelineManager` (`IDLE→LISTENING→PROCESSING→SPEAKING`, barge-in + epoch); `VoiceInteractionManager` (RAM history); `voice.announce` / `voice.status` |
| 7 | Rust `InputManager`; `ScreenCaptureService` + `PrivacyShield`; CPU OCR/CV; `VisionManager` + local VLM; 8 vision/input tools; 28-tool `DEFAULT_TOOL_PERMISSIONS` |
| 8 | `TaskManager` (9 `TaskState`s); `TaskPlanner` DAG; `LoopDetector`; `TaskExecutor`; `VisualRevalidator`; `ModelBenchmarkSuite`; `ResourceManager`; `ProactiveScheduler`; 36 e2e agent integration tests |

## Decisions that still constrain work

| ID | Constraint |
|---|---|
| **041** | Audio IPC is **utterance-buffered** (`audio.capture_start/stop`, `audio.get_speech`, `audio.play_buffer`, `audio.playback_stop`). No streaming binary pipe. 9.0 polls `get_speech`. |
| **042** | `STTProvider` is the STT boundary; FasterWhisper lazy, `asyncio.to_thread`, CPU. |
| **043** | `TTSProvider` is the TTS boundary; Kokoro lazy CPU; `SpeechFormatter` is deterministic (no NLP dep). 9.3 adds `StreamingTTSProvider` as a **subclass** — `TTSProvider` unchanged. |
| **044** | `VoicePipelineManager` owns `IDLE→…→SPEAKING` and barge-in (`stop_audio_playback` + epoch). Do not duplicate this machine in 9.0–9.4; InteractionCore is **9.5**. |
| **045** | Voice is an outer modality, not a toolset. Raw audio stays out of vector memory. |
| **046** | Capture in Python (`mss`/Pillow); frames RAM-only; `PrivacyShield` before perception. |
| **047** | OCR CPU-only (`winocr`); no silent mock fallback in production. |
| **048** | Fast CV is CPU OpenCV contours; `ElementDetectorHook` for later neural detectors. |
| **049** | Cheapest vision tier first; reject VLM if free VRAM **< 1200 MB** or used **> 7000 MB**; no cloud VLM. |
| **050** | OS input only via Rust IPC; `Ctrl+Alt+Shift+Escape`; corner-slam; Python must not import `pyautogui` / `pynput` / `pywin32` for injection. |
| **051** | `TaskState` (9) ≠ `AgentState` (6). `TaskManager` is SoT for long-horizon lifecycle. |
| **052** | Plans are typed DAGs; validate (unique IDs, no cycles, tools exist) before execute; `LoopDetector` on failure loops. |
| **053** | `VisualRevalidator` before `os.input.click` / `type_text` / `hotkey`. |
| **054** | `ResourceManager`: 8 GB VRAM / 16 GB RAM, **500 MB** headroom, `_heavy_lock` LLM vs VLM. |
| **055** | Proactive: enable → user-active preempt → quiet hours **22:00–08:00** (`urgent_health` bypass) → max **1 task / 30 min** → trigger due. Mutating tools still ASK_USER. |

## Phase 8 hardware (qwen3-8b, RTX 4060, `http://127.0.0.1:65400/v1`)

| Metric | SLA | Measured | Status |
|---|---|---|---|
| TTFT | < 1200 ms | **411.31 ms** | PASS |
| Throughput | > 18 tok/s | **25.93** | PASS |
| Schema fidelity | > 95% | **100%** | PASS |
| Peak VRAM | < 7200 MB | **6714.4 MB** (1477.6 MB free) | PASS |

## Phase 9

**9.0 Audio frontend / VAD — COMPLETE.** `MicrophoneLoop` + `EnergyVADDetector` (`VADDetector` ABC, no WebRTC/new deps). Contract: `SpeechStartEvent` → `AudioFrameEvent*` → `SpeechEndEvent`. Hangover endpoint; max utterance 30 s; mute/pause/stop. Tests: `tests/unit/voice/test_microphone_loop.py` (17). **Adapt vs plan:** Python energy VAD; still Decision 041 poll, not a new stream.

**9.1 Streaming STT — COMPLETE.** `StreamingSTTProvider` ABC + `FasterWhisperStreamingAdapter` (lazy, CPU) + `PartialTranscript`. Consumes VAD-delimited PCM from 9.0; emits partials then one committed final transcript. **No second endpointer.** Tests: `tests/unit/voice/test_streaming_stt.py`.

**9.2 LLM streaming + sentence chunking — COMPLETE.** `AgentOrchestrator.stream()` async generator of token/delta events (`run()` unchanged); `SentenceStreamer` emits speakable sentence chunks incrementally. Tests: `tests/unit/agents/test_orchestrator_streaming.py`, `tests/unit/voice/test_sentence_streamer.py`.

**9.3 Streaming TTS & incremental playback — COMPLETE.** `python/tom/voice/streaming_tts.py`: `StreamingTTSProvider(TTSProvider)` yielding ordered frozen `SynthesisChunk(sentence, audio_data, chunk_index, is_last)`; `IncrementalTTSStreamer` runs bounded-concurrency synthesis (worker pool + semaphore), an ordering sequencer, and a separate playback task so **sentence N plays while N+1 synthesizes**. Cancellation watcher discards pending synthesis immediately and calls `EngineClient.stop_audio_playback()` **exactly once**; in-flight synthesis is detached, not awaited. PCM conversion shared with `audio_utils`. Tests: `tests/unit/voice/test_streaming_tts.py` (18) including a deterministic event-log overlap assertion and a no-duplicate-stop test. **Adapt vs plan:** still Decision 041 utterance-buffered `audio.play_buffer` — no streaming binary IPC added.

**9.4 Conversation turn manager & deterministic response policy — COMPLETE.** `python/tom/schemas/turn.py`: `ResponseAction`, `TurnStatus`, `EntityKind`, `EntityReferent`, `BackgroundTaskSnapshot`, `TurnRecord`, `TurnContext` (bounded history + `to_model_context()`), `ResponseDecision`. `python/tom/voice/turn_manager.py`: `ConversationTurnManager` — turn lifecycle (`start_turn` / `record_assistant_reply` / `interrupt_turn` / `reset`), bounded history window, structured entity referents (`last_file`, `last_directory`, `last_query`, `last_screen`, `last_tool`) created from deterministic tool→slot rules and preserved across turns, and Phase 8 `TaskManager` background snapshots (bounded, failure-resilient). Referent **detection** is reported as a fact (`has_anaphoric_reference`, `is_follow_up`); **resolution** is left to the model via structured context — deliberately no NLP/coreference engine. `python/tom/voice/response_policy.py`: `ResponsePolicy` — fixed-precedence rules `empty → silence → progress → ambiguity → destructive-without-target → latency-bearing → direct`, returning `SPEAK_DIRECT` / `FAST_ACK_THEN_WORK` (+ deterministic ack phrase) / `SILENT_ACT` / `CLARIFY` / `REPORT_PROGRESS`. Latency heuristics use **operation type + tool metadata + recorded historical timings** (`record_tool_timing`, bounded 16-sample window), never assumed per-tool durations. Pure, lock-free, no network/LLM. Tests: `tests/unit/voice/test_turn_manager.py` (29) and `tests/unit/voice/test_response_policy.py` (32), including a determinism loop, a JSON-serialisation check, and a < 5 ms evaluation budget assertion.

**9.5+ intentionally deferred** (`InteractionCore` + `TurnExecutor` → 9.6 hardware session + acceptance). No Phase 10 work has started.

**9.B WS0 Measurement Integrity — COMPLETE (2026-10-06).** Fixed first-partial measurement: was 2 ms artifact from lazy generator (generator creation time, not first text yield). True first partial via streaming STT adapter is ~1500 ms from feed start (faster-whisper pads each 100 ms rolling window to 30 s). Fixed `FasterWhisperStreamingAdapter` to mark first partial only when non-empty text is yielded; fixed `TurnExecutor._transcribe` to mark `STT_PARTIAL1` only on first non-empty partial text. Hardware test `test_real_stt_first_partial_meets_sla` documents the deviation (~1500 ms vs 350 ms target). Raw results: `tests/benchmarks/voice/results/stt_tuning_results.json`. **PRELIMINARY** — measured from feed start, not speech onset.

**9.B WS1 Cheap faster-whisper Tuning — COMPLETE (2026-10-06).** Tested beam_size=1,2,3; without_timestamps; condition_on_previous_text=False; cpu_threads 4,6,8,10,12. Raw results: `tests/benchmarks/voice/results/stt_tuning_results.json`. **beam_size=1 is optimal** (~1350-1470 ms p50 vs ~1500 ms baseline, ~10-15% improvement). `without_timestamps`, `condition_on_previous_text=False`, and higher `cpu_threads` all make it slower. The 30 s window padding in faster-whisper is the fundamental bottleneck; flags cannot remove it. **Conclusion: INCONCLUSIVE for meeting SLA target** (350 ms target vs ~1350 ms best achievable). Raw results: `tests/benchmarks/voice/results/stt_tuning_results.json`. **PRELIMINARY** — measured from feed start.

**9.B WS2 Streaming-native STT evaluation — UNDER AUDIT / SUPERSEDED (2026-10-06).** Earlier results superseded by Checkpoint 1 re-measurement.

**9.B WS3 Pipecat component evaluation — UNDER REVISION (2026-10-07).** Earlier claims suspended pending Task 4 re-test with all 15 hesitation clips and H=600 ms.

**9.B Tasks 1–3 Audit & Checkpoint 1 — COMPLETE (2026-10-07).**
- **Task 1 STT accuracy blocker resolved**: Root cause of high WER investigated. Official LibriSpeech samples yield 0.0% WER on sherpa-large [MEASURED: `tests/benchmarks/voice/ws2/results/task1a_bundled_results.json`]. Fixed harness streaming decode loop (`accept_waveform` -> `while is_ready: decode_stream` + tail padding flush). Rerun on all 41 clean recordings: sherpa-large fixed WER is 55.2% (commands 54.1%, questions 49.9%, names 57.5%, numbers 61.7%, 0/8 numbers exact). faster-whisper small.en (beam_size=1) baseline WER is 14.4% (commands 7.2%, questions 11.1%, names 20.3%, numbers 25.8%, 3/8 numbers exact). Confirms domain mismatch for LibriSpeech-trained Zipformer on conversational commands. [MEASURED: `tests/benchmarks/voice/ws2/results/task1c_accuracy_fixed.json`].
- **Task 2 Offline HYBRID-FINAL evaluation**: Evaluated Moonshine-tiny int8 offline via `sherpa_onnx.OfflineRecognizer.from_moonshine`: latency `offline_final_from_endpoint` p50=33.9 ms, p95=57.2 ms (2-3s bucket), overall WER 37.7% (commands 35.2%, questions 25.6%, names 45.2%, numbers 50.0%, 2/8 numbers exact). Meets latency SLA (<350ms) but has elevated WER. NeMo Parakeet TDT verified NOT SUPPORTED in standalone sherpa-onnx 1.10.x CPU runtime (marked NOT FOUND / UNSUPPORTED per prompt allowance). [MEASURED: `tests/benchmarks/voice/ws2/results/task2_offline_candidates.json`].
- **Task 3 Metric fixes**:
  - Explained 'faster-whisper ~4 ms' suspect claim: lazy generator artifact where `model.transcribe()` returns an unconsumed generator; real full decode is p50=1570.9 ms, p95=2195.2 ms.
  - Verified real VAD hangover in TOM config: H=600 ms [SOURCE: `python/tom/voice/microphone.py` L48], superseding unverified H=300 ms assumption.
  - Measured speech onset per recording using energy detector: sherpa-large first partial from onset p50=724.2 ms (subtracts ~1000 ms leading silence in recordings).
  - CPU & spin-wait diagnosis: multi-threaded onnxruntime burns CPU via busy-polling spin-waits (threads=4 peak 1081.7%, threads=2 peak 358.4%, threads=1 avg 6.6% / peak 28.7%). [MEASURED: `tests/benchmarks/voice/ws2/cpu_measurement.py`].
- **Files added**: `tests/benchmarks/voice/ws2/task1a_bundled_test.py`, `task1c_accuracy_fixed.py`, `task2_offline_eval.py`, `summarize_checkpoint1.py`.
- **Status**: Reached Checkpoint 1; review completed.

**9.B Checkpoint 1 Fix Round (F1–F7) — COMPLETE (2026-10-07).**
- **F1 Versions audit**: Dumped `pip freeze` across all venvs (`pip_freeze_prod.txt`, `pip_freeze_ws2.txt`, `pip_freeze_ws3.txt`). Confirmed: prod has onnxruntime==1.30.0, faster-whisper==1.2.1, ctranslate2==4.8.2; ws2 has sherpa-onnx==1.13.8 (newest on PyPI), onnxruntime==1.24.4, faster-whisper==1.2.1. Earlier misreported numbers (1.10.42 / 1.0.3) corrected. No venv changed during Tasks 1–3.
- **F2 Normalizer fix & rescore**: Standardized normalizer (`normalizer.py`, 16/16 unit tests passing; handles hundred/thousand compounds, percent words/symbols, colons, spaced am/pm, ordinals). Rescored all engines (`rescored_accuracy.json`): faster-whisper small.en overall WER dropped to 13.02% (numbers exact 5/8, numbers WER 9.82%); Moonshine-tiny WER 40.92%; sherpa-large WER 55.40%.
- **F3 Clipped-start diagnostic**: A/B evaluation with 0ms, +300ms, +600ms silence prepended (`f3_clipped_start_diagnostic.json`). Prepending silence does not fix first-word errors (faster-whisper FW error rate stays 29.27% - 31.71%). Energy detector confirmed recordings already contain 860–1290 ms pre-speech silence. "TOM" prefix misrecognition is acoustic/LM prior bias, not audio clipping. MicrophoneLoop pre-roll verified at `pre_roll_frames=5` (100 ms) [SOURCE: `python/tom/voice/microphone.py` L51-56].
- **F4 Candidate STT models (HYBRID-FINAL)**: Parakeet 110M empty on HF; Parakeet 0.6B int8 (630 MB) NOT SUPPORTED in sherpa-onnx 1.13.8 (`InitDecoder:303 'vocab_size' does not exist in metadata`). Moonshine-base int8 (273 MB, MIT) fully evaluated (`f4_candidate_models.json`): overall WER 26.55% (commands 13.89%), latency p50=70.42 ms / p95=99.99 ms (2–3 s bucket), peak RAM 349.1 MB, zero high-variance flags.
- **F5 CPU measurement rebuild**: Sampled every 100 ms via psutil across active decode runs (`f5_cpu_rebuild.json`). faster-whisper scales linearly with zero spin-waiting (106% / 205% / 406% of 1 core; 5.3% / 10.2% / 20.3% of machine at 1/2/4 threads). sherpa-onnx multi-threading exhibits spin-waiting (494% / 1298% of 1 core; 24.7% / 64.9% of machine at 2/4 threads). `session.intra_op.allow_spinning` exists in ONNX Runtime but is unexposed in sherpa-onnx C++ bindings.
- **F6 Latency cleanup**: Recomputed with real H=600 ms (`f6_h600_latency.json`): sherpa-large p50=28.98 ms, p95=38.10 ms (stdev/mean = 21.0% -> HIGH-VARIANCE = False). Flagged earlier H=300 ms row (33.5%) and Moonshine-tiny (31.7%) as HIGH-VARIANCE.
- **Files added**: `tests/benchmarks/voice/ws2/normalizer.py`, `rescore_all.py`, `f3_clipped_diagnostic.py`, `eval_candidates_f4.py`, `test_parakeet.py`, `test_moonshine_base.py`, `f5_cpu_rebuild.py`, `measure_h600.py`, `audit_versions.py`.
- **Status**: Fix Round complete; stopping for user review before Task 4.
