# TOM — Current State

Last updated: 2026-10-07

## Now

| Item | Value |
|---|---|
| **Phases 0–8** | CLOSED & COMPLETE |
| **Phase 9** | **IN PROGRESS** — Iterations **9.0–9.4 COMPLETE & VERIFIED**; **9.5 not started**; **9.B WS0–WS1 COMPLETE**; **9.B WS2–WS3 AT CHECKPOINT 1** (audited, suspect claims superseded) |
| **Plan** | `docs/development/PHASE9_IMPLEMENTATIONPLAN.md` |
| **Next** | Checkpoint 1 review; then Task 4 (turn-taking re-test) upon user approval. |
| **Full-repo baseline** | 2026-10-02 Phase 8 exit: **1301 passed** (1218 Python + 83 Rust), **8 skipped**. Ruff/Cargo fmt/clippy clean. **Not rerun** since then. |
| **9.1–9.4 incremental** | Targeted verification **833 passed** (`tests/unit/voice`, `tests/integration/voice`, `tests/unit/agents`, `tests/integration/agents`, `tests/unit/core`, `tests/unit/ipc`). Voice alone: **317**. Full repo **not** re-run. |

## Architecture (locked Phases 0–8; 9.0–9.4 additive)

Python brain ↔ Rust `tom-engine` over Named Pipe `\\.\pipe\tom-engine`.

| Layer | Owns |
|---|---|
| **Python** | Router; `Agent` / `AgentOrchestrator` (6-state; **9.2** `stream()`); 28 tools via `ToolExecutor` + `PermissionEngine`; `MemoryManager`; Phase 6 voice (`STTProvider`, `TTSProvider`, `VoicePipelineManager`, `VoiceInteractionManager`); Phase 7 vision; Phase 8 `TaskManager` / `TaskPlanner` / `TaskExecutor` / `ResourceManager` / `ProactiveScheduler`; **9.0** `MicrophoneLoop` + `VADDetector`; **9.1** `StreamingSTTProvider`; **9.2** `SentenceStreamer`; **9.3** `StreamingTTSProvider` + `IncrementalTTSStreamer`; **9.4** `ConversationTurnManager` + `ResponsePolicy` |
| **Rust** | IPC server; telemetry; `AudioManager` (capture/playback, ring buffer); `InputManager` (`SendInput`, clamp, cancel hotkey) |

Tools (28): system 7, file 6, memory 5, voice 2 (`voice.announce`, `voice.status` SAFE), vision 4 (`SAFE`), OS input 4 (`get_cursor_pos` SAFE; `click` / `type_text` / `hotkey` ASK_USER).

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
- **9.B WS0** Measurement integrity: fixed first-partial measurement (was 2 ms artifact from lazy generator; true first partial ~1500 ms from feed start). Fixed streaming STT adapter to mark first partial only when non-empty text is yielded. **PRELIMINARY** — measured from feed start, not speech onset.
- **9.B WS1** Cheap faster-whisper tuning: tested beam_size=1,2,3; without_timestamps; condition_on_previous_text; cpu_threads 4-12. **beam_size=1 is optimal** (~1350-1470 ms p50 vs ~1500 ms baseline, ~10-15% improvement). without_timestamps, condition_on_previous_text=False, and higher cpu_threads all make it slower. The 30 s window padding in faster-whisper is the fundamental bottleneck. **PRELIMINARY** — measured from feed start.
- **9.B WS2** Streaming-native STT evaluation — **IN PROGRESS (Checkpoint 1 Fix Round complete)**. Evaluated faster-whisper small.en (beam_size=1), sherpa-large (Zipformer streaming), Moonshine-tiny, and Moonshine-base int8 offline across 41 clean recordings using audited single-source normalizer (`normalizer.py`, 16/16 unit tests passing). Rescored accuracy: faster-whisper small.en overall WER 13.02% (commands 7.22%, numbers exact 5/8, numbers WER 9.82%) [MEASURED: `tests/benchmarks/voice/ws2/results/rescored_accuracy.json`]; Moonshine-base overall WER 26.55% (commands 13.89%) [MEASURED: `f4_candidate_models.json`]; Moonshine-tiny WER 40.92%; sherpa-large WER 55.40%. Parakeet 110M NOT FOUND on HF (empty repo); Parakeet 0.6B NOT SUPPORTED in sherpa-onnx 1.13.8 (crashes with `InitDecoder:303 'vocab_size' does not exist in metadata`). Clipped-start diagnostic: prepending 300ms/600ms silence does not improve first-word recognition (pre-speech silence is already 860-1290 ms) [MEASURED: `f3_clipped_start_diagnostic.json`]. MicrophoneLoop pre-roll verified at `pre_roll_frames=5` (100 ms) [SOURCE: `python/tom/voice/microphone.py` L51-56]. Latency with real H=600 ms: sherpa-large p50=28.98 ms; Moonshine-base p50=70.42 ms (2-3s bucket); faster-whisper full decode p50=1570.9 ms. Rebuilt CPU: faster-whisper scales linearly with zero spin-waiting (406% of 1 core / 20.3% of machine at 4 threads); sherpa-onnx multi-threading exhibits spin-waiting (1298% of 1 core / 64.9% of machine at 4 threads); `session.intra_op.allow_spinning` exists in ONNX Runtime but is unexposed in sherpa-onnx [MEASURED: `f5_cpu_rebuild.json`].
- **9.B WS3** Turn detection & Smart Turn — **UNDER REVISION (Queued for Checkpoint 2)**. Previous suspect claims ("Smart Turn 100% false-complete", "VAD hangover 100% correct with H=300ms") superseded pending re-evaluation with all 15 hesitation clips and real config H=600 ms (Task 4).

Files, gaps, and 9.5 contract: **`HANDOFF.md`**. Ledger and ADRs: **`PROGRESS.md`**.
